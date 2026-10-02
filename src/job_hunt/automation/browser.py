"""Browser automation engine using Playwright with stealth context, cookie bypass, frame traversal, and multi-step form handling."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from playwright.async_api import Browser, BrowserContext, Frame, Page, async_playwright

from job_hunt import settings
from job_hunt.automation.captcha_solver import CaptchaSolver, is_captcha_error
from job_hunt.automation.env_scrub import scrubbed_env
from job_hunt.models import ApplicationRecord, CandidateProfile, JobPosting, JobState

logger = logging.getLogger(__name__)

CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    "iframe[src*='cloudflare']",
    ".g-recaptcha",
    ".h-captcha",
    "#cf-turnstile",
    "img[id*='captcha' i]",
    "img[src*='captcha' i]",
    "text=Verify you are human",
]

SUCCESS_INDICATORS = [
    "thank you for applying",
    "application submitted",
    "application received",
    "thank you for your application",
    "we have received your application",
    "thanks for applying",
    "your application was submitted",
    "your application has been submitted",
    "application complete",
    "congratulations",
    "received your submission",
]

CONSENT_ROOTS = [
    "#onetrust-banner-sdk",
    "#CybotCookiebotDialog",
    "#truste-consent-track",
    ".qc-cmp2-container",
    "#usercentrics-root",
    "[id*='cookie' i][class*='banner' i]",
    "[class*='cookie-consent' i]",
    "[aria-label*='cookie' i]",
]

CONSENT_BUTTONS = [
    "#onetrust-accept-btn-handler",
    "#onetrust-button-accept-all",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    ".qc-cmp2-button[mode='primary']",
    "#truste-consent-button",
    "button:has-text('Accept All')",
    "button:has-text('Allow All')",
    "button:has-text('Accept all cookies')",
    "button:has-text('I Agree')",
    "button:has-text('Agree')",
    "button:has-text('Got it')",
]

ATS_LINK_PATTERNS = [
    "ashbyhq.com",
    "greenhouse.io",
    "lever.co",
    "smartrecruiters.com",
    "workable.com",
    "bamboohr.com",
    "recruitee.com",
    "jobvite.com",
    "teamtailor.com",
    "myworkdayjobs.com",
    "/apply",
    "/application",
]

LINKEDIN_EASY_APPLY_SELECTORS = [
    "button:has-text('Easy Apply')",
    "button:has-text('Apply')",
    "a:has-text('Easy Apply')",
    "button:has-text('Apply now')",
    '[data-control-name="jobapply"]',
    ".jobs-apply-button",
    "button.applying-btn",
]

LINKEDIN_SKILL_TAG_SELECTORS = [
    ".jobs-search-results__skill-pill",
    '[data-control-name="jobapply-skills"]',
    ".artdeco-tag-list",
]

CLOUDFLARE_TURNSTILE_SELECTORS = [
    "iframe[src*='turnstile']",
    "#cf-turnstile",
    ".cf-turnstile",
    "div[id^='cf-turnstile']",
    "div[class*='turnstile']",
    "div[class*='Cloudflare']",
]

# Hosts we trust for direct application-URL navigation.
KNOWN_ATS_APEXES = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "workable.com",
)

PORTAL_HOSTS = (
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "remoteok.com",
)


NEGATION_WORDS = {"no", "not", "never", "n't", "decline", "declines", "declined", "disagree", "none"}

DECLINE_SYNONYMS = {"decline", "prefer not", "choose not", "don't wish", "do not wish", "not disclose"}


def _polarity(text: str) -> Optional[bool]:
    """True=affirmative, False=negated, None=neutral (no polarity words)."""
    words = set(re.findall(r"[a-z]+(?:'t)?", text.lower()))
    if words & NEGATION_WORDS:
        return False
    if words & {"yes", "agree", "accept", "confirm"}:
        return True
    return None


AFFIRMATIVE_VARIANTS = {"yeah", "yep", "yea", "affirmative"}
NEGATIVE_VARIANTS = {"nope", "nah", "negative"}

CONFIRMATION_PATTERNS = [
    "passport", "national id", "ssn", "social security",
    "reference name", "reference phone", "references contact",
    "current salary", "salary history", "expected salary amount",
    "bank", "iban", "account number",
    "exact date", "date of birth",
]

# ATS quirks catalog (docs-as-code): per-system behaviors the filler honors.
# Each rule: what to DO and what to NEVER do on that ATS.
ATS_QUIRKS = {
    "ashby": [
        {"id": "email-dedup", "do": "reuse the same email per candidate; Ashby dedups applicants by email",
         "never": "submit twice with different emails to dodge dedup"},
        {"id": "react-inputs", "do": "read back filled values; re-type when the control clears programmatic fill",
         "never": "assume fill() persisted without reading back"},
    ],
    "lever": [
        {"id": "hcaptcha-checkbox", "do": "treat an unsolvable hCaptcha as BLOCKED_CAPTCHA",
         "never": "auto-click captcha checkboxes or outsource solving silently"},
    ],
    "workable": [
        {"id": "spa-refetch", "do": "re-query selectors after each step; the SPA re-renders",
         "never": "cache element handles across steps"},
    ],
    "workday": [
        {"id": "keystroke-typing", "do": "type character-by-character when fill() does not persist",
         "never": "trust a single fill() call without read-back"},
    ],
    "greenhouse": [
        {"id": "embedded-iframe", "do": "search child frames for the form context",
         "never": "assume the form lives in the top document"},
    ],
}


def needs_confirmation(label: str) -> bool:
    """True when an unanswered question needs the human (IDs, references, exact money)."""
    lbl = (label or "").lower()
    return any(p in lbl for p in CONFIRMATION_PATTERNS)

COUNTRY_ALIASES = {
    "usa": "united states",
    "us": "united states",
    "america": "united states",
    "uk": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "uae": "united arab emirates",
    "emirates": "united arab emirates",
}


def _normalize_country(name: str) -> str:
    key = (name or "").strip().lower()
    return COUNTRY_ALIASES.get(key, key)

ATTESTATION_PATTERNS = [
    "certify", "attest", "swear", "penalty of perjury", "under penalty",
    "authorize a background", "authorize background", "background check",
    "drug test", "drug testing", "authorize a drug",
]

ROUTINE_CONSENT_PATTERNS = [
    "privacy policy", "terms of", "terms and conditions",
    "consent to processing", "process my data", "data processing",
]


def checkbox_action(label: str) -> str:
    """Decide a checkbox from its label: "check" or "skip".

    Legal attestations (certifications, background/drug authorizations) are
    never auto-checked — a wrongly-ticked legal box is worse than an unanswered
    question. Only routine processing-consent boxes are checked. Everything
    else (follow-company, newsletters, unknown) is left untouched.
    """
    lbl = (label or "").lower()
    if not lbl.strip():
        return "skip"
    if any(p in lbl for p in ATTESTATION_PATTERNS):
        return "skip"
    if any(p in lbl for p in ROUTINE_CONSENT_PATTERNS):
        return "check"
    return "skip"


def _normalize_variants(text: str) -> str:
    """Fold colloquial yes/no variants to canonical form (word-boundary safe)."""
    out = text.lower()
    for variant in AFFIRMATIVE_VARIANTS:
        out = re.sub(rf"\b{re.escape(variant)}\b", "yes", out)
    for variant in NEGATIVE_VARIANTS:
        out = re.sub(rf"\b{re.escape(variant)}\b", "no", out)
    return out


def match_answer_to_option(answer: str, options: List[str]) -> Optional[str]:
    """Map a resolved answer onto one of the visible options — honestly or not at all.

    Whole-word matching only (answer "No" never matches "Knowledgeable"), with a
    polarity guard (affirmative answers never match negated options and vice
    versa). Decline-style answers match decline-style options. Returns None when
    nothing matches honestly: callers must leave the question unanswered.
    """
    if not answer or not options:
        return None
    norm_ans = _normalize_variants(answer.strip())
    ans_words = set(re.findall(r"[a-z0-9]+", norm_ans))
    ans_polarity = _polarity(norm_ans)

    # 1. Exact (case-insensitive, variant-folded) match wins immediately.
    for opt in options:
        if _normalize_variants(opt.strip()) == norm_ans:
            return opt

    # 2. Decline-style answers match the first decline-style option.
    if any(d in norm_ans for d in DECLINE_SYNONYMS):
        for opt in options:
            if any(d in opt.lower() for d in DECLINE_SYNONYMS):
                return opt
        return None

    # 3. Whole-word containment, polarity-guarded.
    for opt in options:
        norm_opt = _normalize_variants(opt.strip())
        opt_words = set(re.findall(r"[a-z0-9]+", norm_opt))
        if not (ans_words and opt_words):
            continue
        if not (ans_words <= opt_words or opt_words <= ans_words):
            continue
        opt_polarity = _polarity(norm_opt)
        if ans_polarity is not None and opt_polarity is not None and ans_polarity != opt_polarity:
            continue
        if any(d in norm_opt for d in DECLINE_SYNONYMS):
            continue  # never fall back onto a decline variant
        return opt
    return None


def classify_apply_host(url: str) -> str:
    """Classify an apply URL host as "ats", "portal", or "unverified".

    Matching is exact-apex or valid subdomain only, so look-alike prefix tricks
    (evil-greenhouse.io), suffix spoofing (x.greenhouse.io.evil.com), and
    userinfo tricks (greenhouse.io@evil.com) all fail closed to "unverified".
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except Exception:
        return "unverified"
    if parts.scheme not in ("http", "https"):
        return "unverified"
    host = (parts.hostname or "").lower()
    if not host:
        return "unverified"
    for apex in KNOWN_ATS_APEXES:
        if host == apex or host.endswith("." + apex):
            return "ats"
    for portal in PORTAL_HOSTS:
        if host == portal or host.endswith("." + portal):
            return "portal"
    return "unverified"


class BrowserApplicationEngine:
    """Automates form filling and submission on Greenhouse, Lever, Ashby, and standard ATS pages."""

    def __init__(
        self,
        executable_path: str = "/usr/bin/google-chrome",
        headless: bool = True,
        screenshots_dir: str = "data/screenshots",
        timeout_ms: int = 30000,
        captcha_solver: Optional[CaptchaSolver] = None,
        require_linkedin_approval: bool = True,
        linkedin_storage_state: Optional[str] = None,
    ):
        self.executable_path = executable_path
        self.headless = headless
        self.screenshots_dir = Path(screenshots_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_ms = timeout_ms
        self.captcha_solver = captcha_solver or CaptchaSolver()
        self.require_linkedin_approval = require_linkedin_approval
        self.linkedin_storage_state = linkedin_storage_state or "data/linkedin_state.json"
        # (label, required) questions left unanswered by the last fill pass.
        self.last_unanswered: List[tuple] = []
        # Unanswered questions needing the human (IDs, references, exact money).
        self.last_confirmation_needed: List[str] = []

    def submit_blocked_reason(self) -> Optional[str]:
        """Stall-guard: block submit while required questions are unanswered."""
        required = [label for label, is_required in self.last_unanswered if is_required]
        if not required:
            return None
        shown = "; ".join(required[:3])
        return f"Blocked: {len(required)} required question(s) unanswered: {shown}"

    async def verify_fill(self, ctx, fields) -> list:
        """Re-read live field values; return human-readable warnings (empty = OK)."""
        warnings = []
        for f in fields or []:
            try:
                el = f.get("el")
                current = await el.input_value() if el and hasattr(el, "input_value") else ""
            except Exception:
                current = None
            intended = f.get("intended") or ""
            label = f.get("label") or "field"
            if intended and not (current or ""):
                warnings.append(f"fill-mismatch: {label} reads empty after fill")
            if f.get("required") and not (current or ""):
                warnings.append(f"required-empty: {label}")
        return warnings

    async def _is_element_required(self, ctx: Union[Page, Frame], el, label_text: str) -> bool:
        """Best-effort required-field detection: attr, aria, or label asterisk."""
        try:
            for attr in ("required", "aria-required"):
                val = await el.get_attribute(attr)
                if val is not None and str(val).lower() not in ("false", "0"):
                    # Bare `required` present (any value except explicit false) counts.
                    if attr == "required" or str(val).lower() == "true":
                        return True
            if "*" in (label_text or ""):
                return True
        except Exception:
            pass
        return False

    def has_linkedin_session(self) -> bool:
        """Check whether a saved LinkedIn login session exists and looks valid."""
        try:
            state_file = Path(self.linkedin_storage_state)
            if not state_file.exists():
                return False
            import json
            data = json.loads(state_file.read_text(encoding="utf-8"))
            cookies = data.get("cookies", [])
            return any(c.get("name") == "li_at" for c in cookies)
        except Exception:
            return False

    async def _detect_linkedin_login_wall(self, page: Page) -> bool:
        """Detect a LinkedIn auth wall (redirected to login / signup / guest wall)."""
        try:
            url = page.url.lower()
            if "linkedin.com/login" in url or "linkedin.com/signup" in url or "authwall" in url:
                return True
            login_form = await page.query_selector(
                "form.login-form, #login_form, input#session_key"
            )
            if login_form and await login_form.is_visible():
                return True
            authwall = await page.query_selector(
                ".authwall, div[class*='authwall'], div[class*='auth-wall']"
            )
            if authwall and await authwall.is_visible():
                return True
        except Exception:
            pass
        return False

    async def _drop_new_tabs(self, page: Page) -> None:
        """Force same-tab navigation across all frames (lessons from career-ops/diagnose.ts).
        
        Strips target=_blank and redirects window.open to in-tab navigation so clicking 'Apply'
        never opens an unfollowed background tab.
        """
        for fr in page.frames:
            try:
                await fr.evaluate("""() => {
                    document.querySelectorAll('a[target="_blank"], a[target="_new"], form[target]').forEach(el => el.removeAttribute('target'));
                    try {
                        window.open = (u) => {
                            if (u) location.href = u;
                            return null;
                        };
                    } catch(e) {}
                }""")
            except Exception:
                pass

    async def _dismiss_cookie_consent(self, page: Page) -> bool:
        """Detect and auto-dismiss cookie/GDPR consent overlays that obscure forms or buttons."""
        for sel in CONSENT_BUTTONS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=1500)
                    logger.info("Dismissed cookie consent banner via selector [%s]", sel)
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        return False

    async def _try_apply_trigger(self, page: Page) -> bool:
        """If on a job landing page without immediate form inputs, navigate or click to reveal the form.
        
        Lessons from career-ops:
        1. Look for direct links to known ATS (Ashby, Greenhouse, Lever, etc.)
        2. Look for 'Apply' / 'Apply Now' buttons/links and click them in-tab.
        """
        # 1. Look for <a> linking to known ATS
        try:
            for pattern in ATS_LINK_PATTERNS:
                links = await page.query_selector_all(f"a[href*='{pattern}']")
                for link in links:
                    if not await link.is_visible():
                        continue
                    href = await link.get_attribute("href")
                    if href and href.startswith("http") and "submit" not in href.lower():
                        if classify_apply_host(href) == "unverified":
                            logger.warning("Skipping direct navigation to unverified host: %s", href)
                            continue
                        logger.info("Following direct ATS application link: %s", href)
                        await page.goto(href, wait_until="domcontentloaded", timeout=self.timeout_ms)
                        await page.wait_for_timeout(2000)
                        return True
        except Exception:
            pass

        # 2. Look for Apply CTA buttons or links
        apply_selectors = [
            "button:has-text('Apply for this job')",
            "button:has-text('Apply Now')",
            "a:has-text('Apply for this job')",
            "a:has-text('Apply Now')",
            "button:has-text('Start Application')",
            "button:has-text('Apply')",
            "a[href*='apply' i]:not([href*='mailto'])",
        ]
        for sel in apply_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).lower()
                    if any(w in txt for w in ["submit", "applied", "withdraw"]):
                        continue
                    logger.info("Clicking Apply CTA: [%s]", txt.strip())
                    await self._drop_new_tabs(page)
                    await el.click(timeout=3000)
                    await page.wait_for_timeout(2500)
                    return True
            except Exception:
                continue

        return False

    async def _locate_active_form_context(self, page: Page) -> Union[Page, Frame]:
        """Check top-level page and all child frames for form fields.
        
        Greenhouse and Workable embeds render forms inside an <iframe>.
        Returns the Frame or Page containing the application inputs.
        """
        # First check main page
        inputs = await page.query_selector_all("input[type='text'], input[type='email'], input[name*='name' i]")
        if len(inputs) >= 2:
            return page

        # Search child frames
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frame_inputs = await frame.query_selector_all("input[type='text'], input[type='email'], input[name*='name' i]")
                if len(frame_inputs) >= 2:
                    logger.info("Found embedded application form inside iframe: %s", frame.url[:80])
                    return frame
            except Exception:
                continue

        return page

    async def _detect_captcha(self, page: Page) -> bool:
        """Check if page currently presents a CAPTCHA or Cloudflare challenge."""
        for selector in CAPTCHA_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _attempt_captcha_resolution(self, page: Page) -> bool:
        """Attempt automated resolution of audio or visual CAPTCHA if presented."""
        audio_buttons = [
            "button#recaptcha-audio-button",
            "button[title*='audio' i]",
            "button[aria-label*='audio' i]",
            "a[href*='sound' i]",
        ]
        for sel in audio_buttons:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    logger.info("Found audio challenge button: %s. Clicking...", sel)
                    await el.click()
                    await page.wait_for_timeout(1500)
                    break
            except Exception:
                pass

        img_el = await page.query_selector("img[id*='captcha' i], img[src*='captcha' i]")
        captcha_input = await page.query_selector("input[id*='captcha' i], input[name*='captcha' i]")

        if img_el and captcha_input and await img_el.is_visible():
            try:
                img_bytes = await img_el.screenshot()
                import base64
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                code = await self.captcha_solver.solve_image_challenge(b64)
                if code:
                    await captcha_input.fill(code)
                    logger.info("Filled CAPTCHA input with solved code: %s", code)
                    return True
            except Exception as e:
                logger.warning("Automated CAPTCHA solve error: %s", e)

        return False

    async def _fill_field(self, ctx: Union[Page, Frame], selectors: list[str], value: str) -> bool:
        """Attempt to fill an input field trying multiple CSS/XPath selectors.

        Read-back verified: when programmatic fill does not persist (Workday /
        React-controlled inputs), falls back to character-by-character typing.
        """
        for sel in selectors:
            try:
                el = await ctx.query_selector(sel)
                if el and await el.is_visible():
                    # Clear first if needed
                    await el.fill(value)
                    try:
                        if (await el.input_value()) == value:
                            return True
                    except Exception:
                        return True
                    # Fill did not persist: type it instead (Workday quirk).
                    try:
                        await el.click(timeout=1500)
                        await el.fill("")
                        await el.press_sequentially(value, delay=25)
                        if (await el.input_value()) == value:
                            return True
                    except Exception:
                        pass
                    # Neither fill nor typing persisted: try the next selector.
                    continue
            except Exception:
                continue
        return False

    async def _fill_common_fields(self, ctx: Union[Page, Frame], profile: CandidateProfile) -> None:
        """Fill common personal information fields."""
        # First Name
        await self._fill_field(
            ctx,
            ["input[name*='first_name' i]", "input[id*='first_name' i]", "input[aria-label*='first name' i]"],
            profile.first_name,
        )

        # Last Name
        await self._fill_field(
            ctx,
            ["input[name*='last_name' i]", "input[id*='last_name' i]", "input[aria-label*='last name' i]"],
            profile.last_name,
        )

        # Full Name (fallback if no split first/last name)
        await self._fill_field(
            ctx,
            ["input[name*='name' i]:not([name*='first']):not([name*='last'])", "input[id*='name' i]:not([id*='first']):not([id*='last'])"],
            profile.full_name,
        )

        # Email
        await self._fill_field(
            ctx,
            ["input[type='email']", "input[name*='email' i]", "input[id*='email' i]"],
            profile.email,
        )

        # Phone
        await self._fill_field(
            ctx,
            ["input[type='tel']", "input[name*='phone' i]", "input[id*='phone' i]"],
            profile.phone,
        )

        # Location / City
        await self._fill_field(
            ctx,
            ["input[name*='location' i]", "input[id*='location' i]", "input[name*='city' i]"],
            profile.location,
        )

        # LinkedIn
        if profile.linkedin_url:
            await self._fill_field(
                ctx,
                ["input[name*='linkedin' i]", "input[id*='linkedin' i]", "input[aria-label*='linkedin' i]"],
                profile.linkedin_url,
            )

        # GitHub
        if profile.github_url:
            await self._fill_field(
                ctx,
                ["input[name*='github' i]", "input[id*='github' i]", "input[aria-label*='github' i]"],
                profile.github_url,
            )

        # Portfolio / Website
        if profile.portfolio_url:
            await self._fill_field(
                ctx,
                ["input[name*='website' i]", "input[name*='portfolio' i]", "input[id*='website' i]"],
                profile.portfolio_url,
            )

    async def _attach_cv_file(self, ctx: Union[Page, Frame], resume_path: Optional[str]) -> bool:
        """Locate file input and upload candidate CV."""
        if not resume_path or not os.path.exists(resume_path):
            return False

        file_inputs = [
            "input[type='file'][name*='resume' i]",
            "input[type='file'][id*='resume' i]",
            "input[type='file'][aria-label*='resume' i]",
            "input[type='file']",
        ]
        for sel in file_inputs:
            try:
                el = await ctx.query_selector(sel)
                if el:
                    await el.set_input_files(resume_path)
                    logger.info("Attached resume file [%s] via selector [%s]", resume_path, sel)
                    return True
            except Exception as e:
                logger.debug("Failed file input selector %s: %s", sel, e)
                continue
        return False

    async def _get_element_label(self, ctx: Union[Page, Frame], el) -> str:
        """Infer human label or question prompt for an input, textarea, or select."""
        try:
            aria_label = await el.get_attribute("aria-label")
            if aria_label and aria_label.strip():
                return aria_label.strip()

            placeholder = await el.get_attribute("placeholder")
            if placeholder and placeholder.strip() and not any(p in placeholder.lower() for p in ["type", "search", "enter"]):
                return placeholder.strip()

            el_id = await el.get_attribute("id")
            if el_id:
                lbl = await ctx.query_selector(f"label[for='{el_id}']")
                if lbl:
                    txt = await lbl.inner_text()
                    if txt.strip():
                        return txt.strip()

            parent_lbl = await el.evaluate("el => el.closest('label') ? el.closest('label').innerText : ''")
            if parent_lbl and parent_lbl.strip():
                return parent_lbl.strip()

            fg_label = await el.evaluate("""el => {
                const group = el.closest('.form-group, .field, .application-question, div[class*="question"], div[class*="field-entry"], fieldset');
                if (group) {
                    const l = group.querySelector('label, legend, .label, h3, h4, span[class*="label"], [class*="title"]');
                    return l ? l.innerText : '';
                }
                return '';
            }""")
            if fg_label and fg_label.strip():
                return fg_label.strip()

            name_attr = await el.get_attribute("name")
            if name_attr:
                return name_attr.replace("_", " ").title()

        except Exception:
            pass
        return ""

    def _resolve_question_answer(self, label: str, profile: CandidateProfile) -> Optional[str]:
        """Resolve answer to an application question based on profile data and custom answers.
        
        Enhanced with lessons from career-ops/modes/apply.md knockout screening rules.
        """
        if not label:
            return None


        lbl_lower = label.lower()

        # Check explicit custom_answers first
        for k, v in profile.custom_answers.items():
            if k.lower() in lbl_lower or lbl_lower in k.lower():
                return v

        # Work Authorization / Legal Right to Work — tri-split, never blanket Yes.
        # Citizenship and authorization are different questions with different
        # legal answers; unknown country means unanswered, never guessed.
        if any(w in lbl_lower for w in ["citizen of", "citizenship", "are you a citizen"]):
            match = re.search(r"citizen(?:ship)?\s+(?:of|in)\s+([a-zA-Z][a-zA-Z ]+)", label)
            if match and profile.citizenship:
                asked = _normalize_country(match.group(1))
                mine = _normalize_country(profile.citizenship)
                return "Yes" if asked == mine else "No"
            return None
        if any(w in lbl_lower for w in ["authorized to work", "legally authorized", "right to work", "work permit", "work eligibility", "legal right"]):
            match = re.search(r"work\s+(?:in|for)\s+(?:the\s+)?([a-zA-Z][a-zA-Z ]+)", label)
            if match:
                asked = _normalize_country(match.group(1))
                allowed = {_normalize_country(c) for c in (profile.authorized_countries or [])}
                if asked in allowed:
                    return "Yes"
                if asked in (profile.work_authorization or "").lower():
                    return "Yes"
                return None
            return None

        # Sponsorship (Knockout safety)
        if any(w in lbl_lower for w in ["sponsorship", "visa sponsorship", "require sponsorship", "require a visa", "future require"]):
            return "No" if not profile.sponsorship_required else "Yes"

        # Remote / Relocation
        if any(w in lbl_lower for w in ["remote", "work remotely", "telecommute"]):
            return "Yes"
        if any(w in lbl_lower for w in ["willing to relocate", "relocation"]):
            return "Open to remote worldwide" if profile.open_to_remote else "No"

        # Years of Experience
        if any(w in lbl_lower for w in ["years of experience", "how many years", "total experience"]):
            return str(profile.years_of_experience)

        # Notice Period / Start Date
        if any(w in lbl_lower for w in ["notice period", "how soon can you start", "available to start", "start date", "earliest start"]):
            return "Immediate / 2 weeks"

        # Salary / Compensation Expectations
        if any(w in lbl_lower for w in ["salary", "compensation", "desired pay", "rate"]):
            return "Competitive / Negotiable"

        # Current Location / Country
        if any(w in lbl_lower for w in ["current location", "where are you based", "city and country", "residence", "country of residence"]):
            return profile.location

        # Age / Over 18
        if any(w in lbl_lower for w in ["18 years", "at least 18", "age of majority"]):
            return "Yes"

        # Background check
        if any(w in lbl_lower for w in ["background check", "drug test"]):
            return "Yes"

        # How did you hear about us
        if any(w in lbl_lower for w in ["how did you hear", "source", "referral"]):
            return "LinkedIn"

        # Voluntary Self-Identification / Demographics (EEO)
        if any(w in lbl_lower for w in ["gender", "race", "ethnicity", "veteran", "disability", "sexual orientation"]):
            return "I choose not to disclose"

        return None

    async def _fill_questionnaire(self, ctx: Union[Page, Frame], profile: CandidateProfile) -> Dict[str, str]:
        """Dynamically detect and answer custom questions, dropdowns, comboboxes, and radios.

        Anything that cannot be answered honestly is recorded in
        `self.last_unanswered` as (label, required) instead of guessed — the
        submit gate (`submit_blocked_reason`) blocks submission on required ones.
        """
        answers_captured: Dict[str, str] = {}
        self.last_unanswered = []
        self.last_confirmation_needed = []

        # 1. Custom text inputs and textareas
        custom_inputs = await ctx.query_selector_all("input[type='text'], textarea")
        for inp in custom_inputs:
            try:
                val = await inp.input_value()
                if val:
                    continue

                label_text = await self._get_element_label(ctx, inp)
                if not label_text:
                    continue

                ans = self._resolve_question_answer(label_text, profile)
                if ans:
                    await inp.fill(ans)
                    answers_captured[label_text] = ans
                else:
                    self.last_unanswered.append(
                        (label_text, await self._is_element_required(ctx, inp, label_text))
                    )
                    if needs_confirmation(label_text):
                        self.last_confirmation_needed.append(label_text)
            except Exception:
                continue

        # 2. Dropdowns (<select>)
        selects = await ctx.query_selector_all("select")
        for sel in selects:
            try:
                label_text = await self._get_element_label(ctx, sel)
                ans = self._resolve_question_answer(label_text, profile)

                options = await sel.query_selector_all("option")
                opt_texts = [await o.inner_text() for o in options]

                # Honest whole-word mapping; unanswered when nothing matches.
                target_option = match_answer_to_option(ans or "", [t.strip() for t in opt_texts])

                if not target_option and any(w in label_text.lower() for w in ["gender", "race", "veteran", "disability"]):
                    for t in opt_texts:
                        if any(d in t.lower() for d in ["decline", "prefer not", "choose not", "don't wish"]):
                            target_option = t.strip()
                            break

                if target_option:
                    await sel.select_option(label=target_option)
                    answers_captured[label_text or "select"] = target_option
                elif label_text:
                    self.last_unanswered.append(
                        (label_text, await self._is_element_required(ctx, sel, label_text))
                    )
            except Exception:
                continue

        # 2b. Custom comboboxes / React-Select (Greenhouse & Ashby pattern)
        comboboxes = await ctx.query_selector_all("[role='combobox'], div[class*='select__control'], div[class*='react-select']")
        for cb in comboboxes:
            try:
                label_text = await self._get_element_label(ctx, cb)
                ans = self._resolve_question_answer(label_text, profile)
                if ans and await cb.is_visible():
                    await cb.click(timeout=1500)
                    await asyncio.sleep(0.3)
                    # Type answer and press enter
                    await ctx.page.keyboard.type(ans)
                    await asyncio.sleep(0.3)
                    await ctx.page.keyboard.press("Enter")
                    answers_captured[label_text or "combobox"] = ans
            except Exception:
                continue

        # 3. Radio button groups
        radios = await ctx.query_selector_all("input[type='radio']")
        handled_groups = set()
        for radio in radios:
            try:
                group_name = await radio.get_attribute("name")
                if not group_name or group_name in handled_groups:
                    continue
                handled_groups.add(group_name)

                group_label = await self._get_element_label(ctx, radio)
                ans = self._resolve_question_answer(group_label, profile)
                if not ans:
                    if group_label:
                        self.last_unanswered.append(
                            (group_label, await self._is_element_required(ctx, radio, group_label))
                        )
                    continue

                group_radios = await ctx.query_selector_all(f"input[type='radio'][name='{group_name}']")
                candidates: List[str] = []
                candidate_els = []
                for r in group_radios:
                    r_lbl = await self._get_element_label(ctx, r)
                    r_val = (await r.get_attribute("value") or "").lower()
                    candidates.append(r_lbl or r_val)
                    candidate_els.append(r)
                # Honest whole-word mapping; group left untouched on no match.
                picked = match_answer_to_option(ans or "", candidates)
                if picked is not None:
                    await candidate_els[candidates.index(picked)].check()
                    answers_captured[group_label or group_name] = ans
                elif group_label:
                    self.last_unanswered.append(
                        (group_label, await self._is_element_required(ctx, radio, group_label))
                    )
            except Exception:
                continue

        # 4. Checkboxes: routine processing-consent only; attestations and
        # everything else stay untouched and are recorded when required.
        checkboxes = await ctx.query_selector_all("input[type='checkbox']")
        for cb in checkboxes:
            try:
                if await cb.is_checked():
                    continue
                cb_label = await self._get_element_label(ctx, cb)
                if not cb_label:
                    continue
                if checkbox_action(cb_label) == "check" and await cb.is_visible():
                    await cb.check()
                    answers_captured[cb_label] = "checked (routine consent)"
                else:
                    self.last_unanswered.append(
                        (cb_label, await self._is_element_required(ctx, cb, cb_label))
                    )
            except Exception:
                continue

        return answers_captured

    async def _detect_cloudflare_turnstile(self, page: Page) -> bool:
        """Check if page presents a Cloudflare Turnstile challenge."""
        for selector in CLOUDFLARE_TURNSTILE_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _handle_cloudflare_turnstile(self, page: Page) -> bool:
        """Attempt to resolve Cloudflare Turnstile challenge."""
        try:
            # Try clicking the Turnstile iframe
            turnstile_frame = await page.query_selector("iframe[src*='turnstile']")
            if turnstile_frame:
                await turnstile_frame.click(timeout=3000)
                await page.wait_for_timeout(2000)
                return True
            # Try the challenge container
            for sel in CLOUDFLARE_TURNSTILE_SELECTORS[1:]:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=3000)
                    await page.wait_for_timeout(2000)
                    return True
        except Exception:
            pass
        return False

    async def _try_linkedin_external_apply(self, page: Page, allow_unverified: bool = False) -> bool:
        """Handle LinkedIn's 'Apply with external URL' - navigates to the ATS site.

        Untrusted-host guard: anchors pointing at hosts that are neither a known
        ATS apex nor a known portal are skipped unless allow_unverified is set
        (dry-run or explicit human approval). Postings are untrusted data and may
        link anywhere.
        """
        for sel in LINKEDIN_EASY_APPLY_SELECTORS + [
            "a[href*='apply' i]:not([href*='linkedin' i])",
        ]:
            try:
                el = await page.query_selector(sel)
                if el is None:
                    continue
                try:
                    visible = await el.is_visible()
                except Exception:
                    continue
                if not visible:
                    continue
                href = None
                try:
                    href = await el.get_attribute("href")
                except Exception:
                    href = None
                if href and href.strip().lower().startswith("http"):
                    href = href.strip()
                    host_kind = classify_apply_host(href)
                    if host_kind == "unverified" and not allow_unverified:
                        logger.warning("Refusing navigation to unverified apply host: %s", href)
                        continue
                await self._drop_new_tabs(page)
                await el.click(timeout=5000)
                await page.wait_for_timeout(3000)
                return True
            except Exception:
                continue
        return False

    async def _fill_linkedin_easy_apply_side_panel(
        self, page: Page, profile: CandidateProfile
    ) -> bool:
        """Fill the LinkedIn Easy Apply side panel form fields."""
        try:
            await self._fill_common_fields(page, profile)
            await self._fill_linkedin_skills(page, profile)
            await page.wait_for_timeout(1000)

            # Look for submit button inside the side panel
            submit_selectors = [
                "button:has-text('Submit easy apply')",
                "button:has-text('Submit application')",
                "button:has-text('Apply')",
                "button:has-text('Next')",
                "button:has-text('Continue')",
            ]
            for sel in submit_selectors:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=3000)
                    await page.wait_for_timeout(2000)
                    return True
            return True
        except Exception:
            return False

    async def _detect_linkedin_easy_apply_panel(self, page: Page) -> bool:
        """Check if LinkedIn Easy Apply side panel is open.

        Requires genuine apply-modal markers so the guest sign-in wall
        (also an artdeco modal) is never mistaken for an application form.
        """
        try:
            body_text = ""
            modal = await page.query_selector(
                ".artdeco-modal, .jobs-easy-apply-modal, [role='dialog']"
            )
            if modal and await modal.is_visible():
                body_text = ((await modal.inner_text()) or "").lower()
                if "apply to" in body_text or "easy apply" in body_text:
                    return True
        except Exception:
            pass
        panel_selectors = [
            '[data-control-name="jobapply-details"]',
            ".jobs-easy-apply-container",
            "[class*='apply-container']",
        ]
        for sel in panel_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _settle_linkedin_page(self, page: Page) -> None:
        """Let LinkedIn's SPA shell render before running marker checks.

        A single fixed sleep flaps between logged-in shell and wall variants;
        waiting on network idle plus a short settle removes most of the flake.
        """
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(2500)

    async def _is_linkedin_logged_in(self, page: Page) -> bool:
        """Check for logged-in navigation markers, retrying while the SPA settles.

        Uses auth-only nav items (My Network / Messaging / Me menu), which guest
        pages never render — class-based avatar hooks alone miss valid sessions.
        """
        for attempt in range(3):
            for sel in [
                "img.global-nav__me-photo",
                "a.global-nav__me-menu",
                "div.feed-identity-module",
                "button.global-nav__primary-link--active",
                "a:has-text('My Network')",
                "a:has-text('Messaging')",
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        return True
                except Exception:
                    continue
            await page.wait_for_timeout(2500)
        return False

    async def _require_linkedin_session(self, page: Page, job: JobPosting) -> Optional[str]:
        """Enforce a live LinkedIn session right before touching Easy Apply.

        Returns an error message when the modal path cannot proceed, else None.
        External-apply postings never reach this gate.
        """
        if not self.has_linkedin_session():
            return (
                "LinkedIn login required: no saved session "
                "(run scripts/linkedin_login.py on your machine first)"
            )
        if not await self._is_linkedin_logged_in(page):
            return (
                "LinkedIn session invalid (account may be challenged): verify the "
                "account in a normal browser, re-run scripts/linkedin_login.py, "
                "and keep headless volume low"
            )
        return None

    def _job_text_tokens(self, text: str) -> set:
        stop = {
            "senior", "junior", "lead", "staff", "principal", "engineer", "developer",
            "remote", "hybrid", "onsite", "full", "time", "and", "the", "for",
            "with", "iii", "ii", "mid", "level",
        }
        return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in stop}

    async def _detail_pane_matches(self, page: Page, job: JobPosting) -> bool:
        """Check URL, then detail-pane title/company tokens, for the target posting."""
        try:
            if job.external_id and job.external_id in (page.url or ""):
                return True
            want_title = self._job_text_tokens(job.title)
            want_company = (job.company or "").split()[0].lower() if job.company else ""
            title_el = await page.query_selector(
                ".jobs-unified-top-card__job-title, "
                ".job-details-jobs-unified-top-card__job-title, "
                "div.job-view-layout h2"
            )
            if title_el and await title_el.is_visible():
                got_title = self._job_text_tokens(await title_el.inner_text() or "")
                page_text = ((await page.content()) or "").lower()
                title_hit = want_title and len(want_title & got_title) >= max(1, len(want_title) // 2)
                company_hit = (not want_company) or (want_company in page_text[:6000])
                if title_hit and company_hit:
                    logger.info("LinkedIn detail pane matches target posting")
                    return True
        except Exception:
            pass
        return False

    async def _detail_pane_shows_other_job(self, page: Page, job: JobPosting) -> bool:
        """True only with positive evidence the detail pane is a different posting."""
        try:
            want_title = self._job_text_tokens(job.title)
            if not want_title:
                return False
            title_el = await page.query_selector(
                ".jobs-unified-top-card__job-title, "
                ".job-details-jobs-unified-top-card__job-title, "
                "div.job-view-layout h2"
            )
            if title_el and await title_el.is_visible():
                got_title = self._job_text_tokens(await title_el.inner_text() or "")
                if got_title and len(want_title & got_title) < max(1, len(want_title) // 2):
                    return True
        except Exception:
            pass
        return False

    async def _verify_linkedin_target_job(self, page: Page, job: JobPosting) -> bool:
        """Confirm the LinkedIn detail pane shows the target posting.

        Matches on detail-pane title/company tokens (robust against LinkedIn's
        SPA URL games), clicking the matching result card at most once when
        needed. Without positive evidence of a mismatch the page is accepted —
        the Easy Apply modal company guard is the backstop. Falls back to
        posting-ID URL matching.
        """
        try:
            if await self._detail_pane_matches(page, job):
                return True
            want_title = self._job_text_tokens(job.title)
            required = max(1, len(want_title) // 2) if want_title else 0
            if want_title:
                for sel in ("a.job-card-list__title", ".job-card-list__title", ".job-card-container a"):
                    try:
                        cards = await page.query_selector_all(sel)
                    except Exception:
                        continue
                    best = None
                    best_overlap = -1
                    for card in cards:
                        try:
                            if not await card.is_visible():
                                continue
                            card_title = self._job_text_tokens(await card.inner_text() or "")
                            overlap = len(want_title & card_title)
                            if overlap >= required and overlap > best_overlap:
                                best, best_overlap = card, overlap
                        except Exception:
                            continue
                    if best is not None:
                        logger.info("Opening best-matching result card for %s", job.title[:40])
                        try:
                            await best.click(timeout=5000)
                            await page.wait_for_timeout(3000)
                        except Exception:
                            pass
                        return await self._detail_pane_matches(page, job)
            # No positive evidence of a mismatch: accept, modal guard backstops.
            return not await self._detail_pane_shows_other_job(page, job)
        except Exception:
            pass
        return False

    async def _try_linkedin_easy_apply(self, page: Page, job: Optional[JobPosting] = None) -> bool:
        """Handle LinkedIn's 'Easy Apply' flow which uses a side panel React modal.

        The Apply button is scoped to the job-detail pane first so a search view
        can never trigger an application for a neighboring job card.
        """
        scoped_selectors = [
            ".jobs-unified-top-card button:has-text('Easy Apply')",
            ".job-details-jobs-unified-top-card__container button:has-text('Easy Apply')",
            "div.job-view-layout button:has-text('Easy Apply')",
            "main button:has-text('Easy Apply')",
        ]
        for sel in scoped_selectors + LINKEDIN_EASY_APPLY_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await page.wait_for_timeout(1000)
                    await el.click(timeout=5000)
                    await page.wait_for_timeout(3000)
                    if await self._detect_linkedin_easy_apply_panel(page):
                        logger.info("LinkedIn Easy Apply panel opened successfully")
                        return True
                    iframe = await page.query_selector('iframe[src*="linkedin"]')
                    if iframe:
                        logger.info("LinkedIn application iframe detected")
                        return True
                    return True
            except Exception:
                continue
        return False

    async def _verify_easy_apply_modal_company(self, page: Page, job: JobPosting) -> bool:
        """Confirm the open Easy Apply modal targets our company, not a neighbor card.

        The modal heading reads 'Apply to {Company}'. The company name is pulled
        with a text pattern instead of a tag selector so heading-tag changes can
        never silently skip the check. A mismatch aborts the run so a live submit
        can never go to the wrong employer.
        """
        try:
            company_token = (job.company or "").split()[0].lower() if job.company else ""
            for _ in range(5):
                modal = await page.query_selector(
                    ".artdeco-modal, .jobs-easy-apply-modal, [role='dialog']"
                )
                if modal is None or not await modal.is_visible():
                    return True  # no modal found; nothing to contradict
                text = ((await modal.inner_text()) or "").strip().lower()
                match = re.search(r"apply to\s+([a-z0-9][a-z0-9 .&'-]{1,60})", text)
                if not match:
                    # Modal still loading (spinner) or no company claim yet; retry.
                    await page.wait_for_timeout(2000)
                    continue
                modal_company = match.group(1).strip()
                if company_token and company_token not in modal_company:
                    logger.warning(
                        "Easy Apply modal mismatch: modal targets %r, job is %r",
                        modal_company, job.company,
                    )
                    return False
                return True
            logger.warning("Easy Apply modal company never resolved; aborting to be safe")
            return False
        except Exception:
            return True

    async def _fill_linkedin_skills(self, page: Page, profile: CandidateProfile) -> bool:
        """Add skills tags on LinkedIn's application form if present."""
        skills_added = 0
        for skill in profile.verified_skills[:5]:
            try:
                # Look for skill input/combobox
                skill_input = await page.query_selector(
                    'input[placeholder*="skill" i], input[placeholder*="search" i], [role="combobox"]'
                )
                if skill_input and await skill_input.is_visible():
                    await skill_input.fill(skill)
                    await page.wait_for_timeout(500)
                    # Press Enter to add the tag
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(500)
                    skills_added += 1
                    logger.info("Added LinkedIn skill tag: %s", skill)
            except Exception:
                continue
        return skills_added > 0

    async def _handle_steppers_and_submit(
        self,
        ctx: Union[Page, Frame],
        page: Page,
        profile: CandidateProfile,
        max_steps: int = 5,
    ) -> Tuple[Optional[Any], bool]:
        """Handle multi-page/stepper applications by clicking 'Next' until 'Submit' is reached."""
        for step in range(max_steps):
            # Check for final submit button
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "input#nextButton",
                "button:has-text('Submit Application')",
                "button:has-text('Submit application')",
                "button:has-text('Submit')",
                "button:has-text('Apply')",
                "button:has-text('Send Application')",
                "button:has-text('Complete Application')",
                "button:has-text('Continue application')",
                "#submit_app",
                "[data-qa='btn-submit']",
                "button:has-text('Save and Continue')",
            ]
            for sel in submit_selectors:
                try:
                    el = await ctx.query_selector(sel)
                    if el and await el.is_visible():
                        txt = (await el.inner_text() or await el.get_attribute("value") or "").lower()
                        if any(w in txt for w in ["submit", "apply", "send", "finish", "continue"]):
                            return el, True
                except Exception:
                    continue

            # If no submit button, check for Next / Continue / Step button
            next_selectors = [
                "button:has-text('Next')",
                "button:has-text('Continue')",
                "button:has-text('Save & Continue')",
                "button:has-text('Review')",
                "button:has-text('Next Step')",
                "input[value*='Next' i]",
                "input[value*='Continue' i]",
                "button:has-text('Save and continue')",
            ]
            next_btn = None
            for sel in next_selectors:
                try:
                    el = await ctx.query_selector(sel)
                    if el and await el.is_visible():
                        next_btn = el
                        break
                except Exception:
                    continue

            if next_btn:
                logger.info("Stepper progress: clicking Next / Continue on step %d", step + 1)
                await next_btn.click()
                await page.wait_for_timeout(2000)
                await self._fill_common_fields(ctx, profile)
                await self._fill_questionnaire(ctx, profile)
            else:
                break

        return None, False

    async def fill_and_submit(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        resume_file_path: Optional[str] = None,
        dry_run: bool = False,
        linkedin_approved: bool = False,
    ) -> ApplicationRecord:
        """Navigate to application page, fill form, and autonomously submit or dry-run."""
        record = ApplicationRecord(
            job_id=job.id or 0,
            state=JobState.APPLICATION_STARTED,
            submission_payload={"url": job.raw_url, "dry_run": dry_run},
        )

        if not settings.linkedin_enabled() and settings.is_linkedin_job(job):
            record.state = JobState.FAILED
            record.error_message = (
                "Blocked: LinkedIn applications are disabled "
                "(LINKEDIN_ENABLED=false / linkedin_enabled=false)"
            )
            logger.warning("Job %s blocked: %s", job.id, record.error_message)
            return record

        async with async_playwright() as p:
            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
            exec_path = self.executable_path if os.path.exists(self.executable_path) else None
            browser = await p.chromium.launch(
                executable_path=exec_path,
                headless=self.headless,
                args=launch_args,
                env=scrubbed_env(),
            )
            context_kwargs: Dict[str, Any] = {
                "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 800},
                "locale": "en-US",
                "timezone_id": "America/New_York",
            }
            is_linkedin = "linkedin.com" in job.raw_url.lower()
            if is_linkedin and self.has_linkedin_session():
                context_kwargs["storage_state"] = self.linkedin_storage_state
                logger.info("Using saved LinkedIn login session")
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()

            # Inject stealth evasions to mask Playwright/Selenium traces
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {}, csi: function(){}, loadTimes: function(){} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
                Object.defineProperty(navigator, 'platform', { get: () => 'Linux x86_64' });
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
                const _query = window.navigator.permissions.query.bind(window.navigator.permissions);
                window.navigator.permissions.query = (p) => (
                    p.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : _query(p)
                );
            """)

            try:
                is_linkedin = "linkedin.com" in job.raw_url.lower()
                start_url = job.raw_url
                if is_linkedin and job.external_id:
                    start_url = f"https://www.linkedin.com/jobs/view/{job.external_id}/"
                logger.info("Navigating to application URL: %s", start_url)
                await page.goto(start_url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)

                # Strip target=_blank across all frames so navigation stays in-tab
                await self._drop_new_tabs(page)

                # Auto-dismiss cookie/GDPR consent banner
                await self._dismiss_cookie_consent(page)

                # Detect if this is a LinkedIn job page
                if is_linkedin:
                    logger.info("LinkedIn job detected - application type: %s", job.application_type or "unknown")

                # LinkedIn approval gate: never submit without explicit human approval
                # (dry-runs only fill + screenshot and are always allowed).
                if is_linkedin and not dry_run and self.require_linkedin_approval and not linkedin_approved:
                    record.state = JobState.FAILED
                    record.error_message = (
                        "Blocked: LinkedIn submit requires human approval "
                        "(dry_run=False without linkedin_approved=True)"
                    )
                    logger.warning("Job %s blocked: %s", job.id, record.error_message)
                    return record

                # LinkedIn auth wall: hard-block only when the page itself is walled.
                # Session validity is enforced lazily at the Easy Apply modal step:
                # external-apply postings ("responses managed off LinkedIn") need
                # no session — the guest page plus the outbound Apply link suffice.
                await self._settle_linkedin_page(page)
                if is_linkedin and await self._detect_linkedin_login_wall(page):
                    screenshot_file = str(self.screenshots_dir / f"linkedin_login_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file, full_page=True)
                    record.state = JobState.FAILED
                    record.screenshot_path = screenshot_file
                    record.error_message = (
                        "LinkedIn login wall present: re-run scripts/linkedin_login.py "
                        "and verify the account is not challenged in a normal browser"
                    )
                    logger.warning("Job %s blocked: %s", job.id, record.error_message)
                    return record

                # 1. Check for CAPTCHA (including Cloudflare Turnstile) and attempt resolution
                captcha_detected = await self._detect_captcha(page) or (is_linkedin and await self._detect_cloudflare_turnstile(page))
                if captcha_detected:
                    logger.info("CAPTCHA detected on job %s. Attempting automated resolution...", job.id)
                    if is_linkedin:
                        turnstile_solved = await self._handle_cloudflare_turnstile(page)
                        if turnstile_solved:
                            await page.wait_for_timeout(2000)
                            captcha_detected = await self._detect_captcha(page) or await self._detect_cloudflare_turnstile(page)
                    solved = await self._attempt_captcha_resolution(page) if captcha_detected else False
                    if not solved:
                        screenshot_file = str(self.screenshots_dir / f"captcha_job_{job.id}.png")
                        await page.screenshot(path=screenshot_file, full_page=True)
                        record.state = JobState.BLOCKED_CAPTCHA
                        record.screenshot_path = screenshot_file
                        record.error_message = "CAPTCHA or Cloudflare challenge detected and unresolvable"
                        logger.warning("Job %s blocked by CAPTCHA: %s", job.id, screenshot_file)
                        return record

                # 2. Handle LinkedIn application types
                if is_linkedin:
                    # Canonical detail URL keeps logged-in sessions on the posting
                    # instead of bouncing to a search view (wrong-job risk).
                    if job.external_id:
                        canonical_view = f"https://www.linkedin.com/jobs/view/{job.external_id}/"
                        if job.external_id not in page.url:
                            logger.info("Navigating to canonical LinkedIn posting view")
                            await page.goto(canonical_view, timeout=self.timeout_ms, wait_until="domcontentloaded")
                            await page.wait_for_timeout(2000)
                            await self._drop_new_tabs(page)
                            await self._dismiss_cookie_consent(page)
                    if not await self._verify_linkedin_target_job(page, job):
                        record.state = JobState.FAILED
                        record.error_message = "LinkedIn page does not show the target posting"
                        return record
                    if job.application_type == "external_url":
                        logger.info("LinkedIn External Apply - navigating to external ATS")
                        external_opened = await self._try_linkedin_external_apply(
                            page, allow_unverified=(dry_run or linkedin_approved)
                        )
                        if external_opened:
                            await self._drop_new_tabs(page)
                            await self._dismiss_cookie_consent(page)
                            ctx = await self._locate_active_form_context(page)
                            await self._fill_common_fields(ctx, profile)
                            answers = await self._fill_questionnaire(ctx, profile)
                            if answers:
                                record.submission_payload["answers"] = answers
                                record.submission_payload["needs_confirmation"] = self.last_confirmation_needed
                            if resume_file_path:
                                await self._attach_cv_file(ctx, resume_file_path)
                            submit_btn, found = await self._handle_steppers_and_submit(ctx, page, profile)
                        else:
                            record.state = JobState.FAILED
                            record.error_message = "Could not navigate to external ATS from LinkedIn"
                            return record
                    elif job.application_type == "easy_apply":
                        logger.info("LinkedIn Easy Apply - clicking Easy Apply button")
                        session_error = await self._require_linkedin_session(page, job)
                        if session_error:
                            record.state = JobState.FAILED
                            record.error_message = session_error
                            logger.warning("Job %s blocked: %s", job.id, session_error)
                            return record
                        easy_apply_opened = await self._try_linkedin_easy_apply(page, job)
                        if easy_apply_opened:
                            if not await self._verify_easy_apply_modal_company(page, job):
                                screenshot_file = str(self.screenshots_dir / f"wrong_job_{job.id}.png")
                                await page.screenshot(path=screenshot_file, full_page=True)
                                record.state = JobState.FAILED
                                record.screenshot_path = screenshot_file
                                record.error_message = "Easy Apply opened for a different employer; aborted"
                                return record
                            logger.info("LinkedIn Easy Apply opened - filling side panel")
                            await self._fill_linkedin_easy_apply_side_panel(page, profile)
                            await page.wait_for_timeout(2000)
                        else:
                            record.state = JobState.FAILED
                            record.error_message = "Could not open LinkedIn Easy Apply panel"
                            return record
                    else:
                        logger.info("LinkedIn apply type unknown - trying Easy Apply first, then external")
                        session_error = await self._require_linkedin_session(page, job)
                        if session_error:
                            logger.info(
                                "Easy Apply unavailable (%s); falling back to external link",
                                session_error,
                            )
                            easy_apply_opened = False
                        else:
                            easy_apply_opened = await self._try_linkedin_easy_apply(page, job)
                        if easy_apply_opened:
                            if not await self._verify_easy_apply_modal_company(page, job):
                                screenshot_file = str(self.screenshots_dir / f"wrong_job_{job.id}.png")
                                await page.screenshot(path=screenshot_file, full_page=True)
                                record.state = JobState.FAILED
                                record.screenshot_path = screenshot_file
                                record.error_message = "Easy Apply opened for a different employer; aborted"
                                return record
                            await self._fill_linkedin_easy_apply_side_panel(page, profile)
                        else:
                            external_opened = await self._try_linkedin_external_apply(
                                page, allow_unverified=(dry_run or linkedin_approved)
                            )
                            if external_opened:
                                ctx = await self._locate_active_form_context(page)
                                await self._fill_common_fields(ctx, profile)
                                submit_btn, found = await self._handle_steppers_and_submit(ctx, page, profile)

                    # Check for success after LinkedIn flow
                    if record.state not in (JobState.FAILED, JobState.BLOCKED_CAPTCHA):
                        if dry_run:
                            screenshot_file = str(self.screenshots_dir / f"dry_run_job_{job.id}.png")
                            await page.screenshot(path=screenshot_file, full_page=True)
                            record.state = JobState.SUBMITTED
                            record.screenshot_path = screenshot_file
                            record.confirmation_text = "DRY RUN: LinkedIn application filled and verified without submit."
                        else:
                            page_text = await page.content()
                            has_success = any(ind in page_text.lower() for ind in SUCCESS_INDICATORS)
                            screenshot_file = str(self.screenshots_dir / f"linkedin_result_job_{job.id}.png")
                            await page.screenshot(path=screenshot_file)
                            record.screenshot_path = screenshot_file
                            if has_success:
                                record.state = JobState.SUBMITTED
                                record.confirmation_text = "LinkedIn application submitted and confirmed"
                            else:
                                record.state = JobState.APPLICATION_STARTED
                                record.confirmation_text = "LinkedIn application processed"
                    return record

                # Non-LinkedIn flow: check if form inputs exist; if not, trigger 'Apply' CTA
                initial_inputs = await page.query_selector_all("input[type='text'], input[type='email']")
                if len(initial_inputs) < 2:
                    logger.info("No direct form inputs on landing page for Job %s. Triggering Apply CTA...", job.id)
                    triggered = await self._try_apply_trigger(page)
                    if triggered:
                        await self._drop_new_tabs(page)
                        await self._dismiss_cookie_consent(page)

                # Locate active form context (top page or embedded iframe)
                ctx = await self._locate_active_form_context(page)

                # Fill form fields
                await self._fill_common_fields(ctx, profile)

                # Fill dynamic questionnaire (questions, radios, dropdowns, comboboxes)
                answers = await self._fill_questionnaire(ctx, profile)
                if answers:
                    record.submission_payload["answers"] = answers
                    record.submission_payload["needs_confirmation"] = self.last_confirmation_needed
                    logger.info("Answered %d dynamic form questions on job %s", len(answers), job.id)

                # Attach CV file
                if resume_file_path:
                    await self._attach_cv_file(ctx, resume_file_path)

                # If dry-run mode, capture screenshot and return success without clicking submit
                if dry_run:
                    screenshot_file = str(self.screenshots_dir / f"dry_run_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file, full_page=True)
                    record.state = JobState.SUBMITTED
                    record.screenshot_path = screenshot_file
                    record.confirmation_text = "DRY RUN: Form filled and verified successfully without submit."
                    return record

                # Handle multi-step forms and locate final submit button
                submit_btn, found = await self._handle_steppers_and_submit(ctx, page, profile)

                if not submit_btn:
                    screenshot_file = str(self.screenshots_dir / f"no_submit_btn_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file)
                    record.state = JobState.FAILED
                    record.screenshot_path = screenshot_file
                    record.error_message = "Could not locate a visible Submit button"
                    return record

                # Stall-guard: never submit with required questions unanswered.
                blocked = self.submit_blocked_reason()
                if blocked:
                    screenshot_file = str(self.screenshots_dir / f"unanswered_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file, full_page=True)
                    record.state = JobState.FAILED
                    record.screenshot_path = screenshot_file
                    record.error_message = blocked
                    record.submission_payload["unanswered"] = self.last_unanswered
                    logger.warning("Job %s submit blocked: %s", job.id, blocked)
                    return record

                # 8. Click submit with scroll-into-view and fallback
                try:
                    await submit_btn.scroll_into_view_if_needed()
                    await submit_btn.click(timeout=5000)
                except Exception:
                    # Fallback to evaluating click in element's context
                    await submit_btn.evaluate("el => el.click()")

                await page.wait_for_timeout(5000)

                # 9. Check for submission errors vs confirmation
                content = await page.content()
                if is_captcha_error(content):
                    screenshot_file = str(self.screenshots_dir / f"captcha_reject_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file)
                    record.state = JobState.BLOCKED_CAPTCHA
                    record.screenshot_path = screenshot_file
                    record.error_message = "Submission rejected: Invalid CAPTCHA"
                    return record

                # Check for field validation errors
                field_errors = await page.query_selector_all(".error, [aria-invalid='true'], .field-error, .application-error")
                visible_errors = []
                for fe in field_errors:
                    if await fe.is_visible():
                        visible_errors.append((await fe.inner_text()).strip())

                page_text = content.lower()
                current_url = page.url.lower()

                has_success = (
                    any(ind in page_text for ind in SUCCESS_INDICATORS)
                    or "thank" in current_url
                    or "confirm" in current_url
                    or "success" in current_url
                    or "applied" in current_url
                )

                screenshot_file = str(self.screenshots_dir / f"submission_result_job_{job.id}.png")
                await page.screenshot(path=screenshot_file)
                record.screenshot_path = screenshot_file

                if has_success:
                    record.state = JobState.SUBMITTED
                    record.confirmation_text = "Application submitted and confirmed"
                elif visible_errors:
                    record.state = JobState.FAILED
                    record.error_message = f"Field validation errors: {'; '.join(visible_errors[:3])}"
                else:
                    record.state = JobState.FAILED
                    record.error_message = "Submitted, but could not detect confirmation text"

                return record

            except Exception as e:
                logger.error("Browser automation error for job %s: %s", job.id, e)
                screenshot_file = str(self.screenshots_dir / f"error_job_{job.id}.png")
                try:
                    await page.screenshot(path=screenshot_file)
                    record.screenshot_path = screenshot_file
                except Exception:
                    pass
                record.state = JobState.FAILED
                record.error_message = str(e)
                return record

            finally:
                await context.close()
                await browser.close()
