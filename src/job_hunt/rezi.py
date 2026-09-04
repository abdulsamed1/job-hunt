"""Rezi Pro integration and MCP server connection configuration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

REZI_MCP_SERVER_URL = "https://api.rezi.ai/mcp"
REZI_CHROME_EXTENSION_URL = "https://chromewebstore.google.com/detail/rezi-ai-autofill-job-appl/jkcdmgcaamddgioenedkdhbegbaokcek"
DEFAULT_REZI_CONFIG_PATH = Path("config/rezi.json")


class ReziIntegration:
    """Manages Rezi Pro integration settings, MCP server definitions, and authentication config."""

    def __init__(self, config_path: Path | str = DEFAULT_REZI_CONFIG_PATH):
        self.config_path = Path(config_path)
        self.config = self._load_or_create_config()

    def _load_or_create_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Could not read Rezi config at %s: %s", self.config_path, e)

        default_conf: Dict[str, Any] = {
            "account": {
                "email": "alifayeez67@gmail.com",
                "tier": "Pro",
            },
            "mcp": {
                "server_url": REZI_MCP_SERVER_URL,
                "transport": "streamable-http",
            },
            "chrome_extension": {
                "url": REZI_CHROME_EXTENSION_URL,
                "extension_id": "jkcdmgcaamddgioenedkdhbegbaokcek",
            },
            "options": {
                "preserve_master_pdf": True,
                "master_pdf_path": "/home/abdu/production/job-hunt/Abdulsamed_Hamdy.pdf",
            },
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(default_conf, f, indent=2)
        return default_conf

    def get_mcp_settings_snippet(self) -> Dict[str, Any]:
        """Generate MCP client configuration block for Cursor, Gemini CLI, Claude, and Codex."""
        return {
            "mcpServers": {
                "rezi": {
                    "url": REZI_MCP_SERVER_URL,
                    "transport": "streamable-http",
                }
            }
        }
