"""Shared, non-secret runtime settings for the Web app and MCP connector."""

import hashlib
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_BUSINESS_DB = PROJECT_ROOT / "data" / "wuma_ai.db"
DEFAULT_DEMO_DB = PROJECT_ROOT / "data" / "wuma_ai_demo.db"


def demo_mode_enabled():
    return os.getenv("WUMA_DEMO_MODE", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def database_path():
    configured = os.getenv("WUMA_AI_DB_PATH", "").strip()
    path = Path(configured).expanduser() if configured else (
        DEFAULT_DEMO_DB if demo_mode_enabled() else DEFAULT_BUSINESS_DB
    )
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def database_id(path):
    canonical = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
