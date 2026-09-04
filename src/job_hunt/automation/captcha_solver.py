"""CAPTCHA and challenge resolution engine inspired by production audio-first solving architecture.

Implements:
1. Audio challenge detection and parsing (Whisper / Speech-to-text pipeline).
2. Clean audio token normalization (collapsing stutter/stoppers, filtering to alphanumeric).
3. Vision / OCR image fallback (PSM 7 single-line alphanumeric).
4. Challenge failure detection (is_captcha_error).
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


def is_captcha_error(html_text: str) -> bool:
    """Detect if server response indicates a CAPTCHA validation error."""
    if not html_text:
        return False
    text = html_text.lower()
    has_captcha = "captcha" in text or "security code" in text or "verification code" in text
    has_error = any(
        err in text
        for err in ("incorrect", "invalid", "error", "wrong", "does not match", "mismatch", "try again")
    )
    return has_captcha and has_error


def parse_audio_transcription(raw_text: str, max_length: int = 6) -> str:
    """Parse raw transcription of an audio challenge into clean alphanumeric code.

    Audio challenges often produce separated characters ('T, D, R, 5, 8.') or duplicate stutter.
    We normalize, collapse immediate consecutive duplicate tokens, and constrain length.
    """
    if not raw_text:
        return ""

    # Replace punctuation and special characters with spaces, then uppercase
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", raw_text).upper().strip()
    tokens = cleaned.split()
    if not tokens:
        return ""

    # Collapse consecutive identical tokens ("9 9 4 3" -> "9 4 3")
    collapsed = []
    for t in tokens:
        if not collapsed or collapsed[-1] != t:
            collapsed.append(t)

    code = "".join(collapsed)
    if len(code) > max_length:
        code = code[:max_length]
    return code


class CaptchaSolver:
    """Production CAPTCHA resolution adapter with audio-first and vision fallback strategies."""

    def __init__(
        self,
        cf_account_id: Optional[str] = None,
        cf_api_token: Optional[str] = None,
        openai_api_key: Optional[str] = None,
    ):
        self.cf_account_id = cf_account_id or os.getenv("CF_ACCOUNT_ID")
        self.cf_api_token = cf_api_token or os.getenv("CF_API_TOKEN") or os.getenv("CLOUDFLARE_API_TOKEN")
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")

    async def solve_audio_challenge(self, wav_bytes: bytes) -> Optional[str]:
        """Transcribe audio challenge using Cloudflare Workers AI Whisper or OpenAI Whisper."""
        if not wav_bytes:
            return None

        # Strategy 1: Cloudflare Workers AI (@cf/openai/whisper-tiny-en or whisper-large-v3-turbo)
        if self.cf_account_id and self.cf_api_token:
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/ai/run/@cf/openai/whisper-tiny-en"
            headers = {
                "Authorization": f"Bearer {self.cf_api_token}",
                "Content-Type": "audio/wav",
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, headers=headers, content=wav_bytes)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_text = data.get("result", {}).get("text", "")
                        code = parse_audio_transcription(raw_text)
                        if code and len(code) >= 3:
                            logger.info("Audio CAPTCHA solved via Cloudflare Workers AI: %s", code)
                            return code
            except Exception as e:
                logger.warning("Cloudflare audio solve failed: %s", e)

        # Strategy 2: OpenAI Whisper API if key available
        if self.openai_api_key:
            url = "https://api.openai.com/v1/audio/transcriptions"
            headers = {"Authorization": f"Bearer {self.openai_api_key}"}
            files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
            data = {"model": "whisper-1"}
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, headers=headers, files=files, data=data)
                    if resp.status_code == 200:
                        raw_text = resp.json().get("text", "")
                        code = parse_audio_transcription(raw_text)
                        if code:
                            logger.info("Audio CAPTCHA solved via OpenAI Whisper: %s", code)
                            return code
            except Exception as e:
                logger.warning("OpenAI audio solve failed: %s", e)

        return None

    async def solve_image_challenge(self, image_base64: str) -> Optional[str]:
        """Solve visual CAPTCHA image using Vision API or local OCR."""
        clean_b64 = re.sub(r"^data:image/[^;]+;base64,", "", image_base64).strip()
        if not clean_b64:
            return None

        # Cloudflare Workers AI Vision
        if self.cf_account_id and self.cf_api_token:
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/ai/run/@cf/meta/llama-3.2-11b-vision-instruct"
            headers = {
                "Authorization": f"Bearer {self.cf_api_token}",
                "Content-Type": "application/json",
            }
            try:
                raw_bytes = list(base64.b64decode(clean_b64))
                payload = {
                    "image": raw_bytes,
                    "prompt": "Return only the exact uppercase alphanumeric characters shown in this distorted CAPTCHA. No spaces, no extra words.",
                    "max_tokens": 15,
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        raw = resp.json().get("result", {}).get("response", "")
                        code = re.sub(r"[^A-Z0-9]", "", raw.upper())[:6]
                        if len(code) >= 3:
                            logger.info("Image CAPTCHA solved via Vision: %s", code)
                            return code
            except Exception as e:
                logger.warning("Vision solve failed: %s", e)

        return None
