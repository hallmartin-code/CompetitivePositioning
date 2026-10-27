"""Central configuration. Secrets come from the environment and are never logged."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# A project-local .env wins over an ambient shell variable, matching the other
# TEN Capital tools: a stale machine-wide key should not silently bill the wrong
# account when a developer has just written a key into this project's .env.
load_dotenv(PROJECT_ROOT / ".env", override=True)

ASSETS_DIR = PROJECT_ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
LOGO_PATH = ASSETS_DIR / "TEN_Capital_logo_footer.png"
# Streamlit's public directory, served at /app/static/ (server.enableStaticServing).
STATIC_DIR = PROJECT_ROOT / "static"
FAVICON_PATH = STATIC_DIR / "favicon.png"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR") or PROJECT_ROOT / "output")

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("ANTHROPIC_EFFORT", "high")
MAX_OUTPUT_TOKENS = 32000

# Optional explicit path to soffice(.exe) for legacy .ppt conversion.
LIBREOFFICE_PATH = os.environ.get("LIBREOFFICE_PATH") or None
PPT_CONVERT_TIMEOUT_S = 180

# Pages with less extractable text than this are treated as image-only.
MIN_PAGE_CHARS = 12

# Hard cap on source text sent to the model (characters, roughly 4 per token).
# Well inside the context window; exceeding it is reported, never silently cut.
MAX_SOURCE_CHARS = 600_000

# ---------------------------------------------------------------------------
# Result emails (Resend)
# ---------------------------------------------------------------------------
# Each generated analysis is emailed with the PDF and draft attached. This sends
# deck-derived content to Resend and the recipient's inbox, so it runs only when
# a key is configured, and the UI discloses it.

RESEND_API_KEY = (os.environ.get("RESEND_API_KEY") or "").strip() or None
NOTIFY_EMAIL_TO = os.environ.get("NOTIFY_EMAIL_TO", "Info@tencapital.group")
# The sending domain must be verified in Resend or the API rejects the send.
NOTIFY_EMAIL_FROM = os.environ.get(
    "NOTIFY_EMAIL_FROM", "TEN Capital Competition Analysis <noreply@tencapital.group>")
EMAIL_NOTIFICATIONS = (os.environ.get("EMAIL_NOTIFICATIONS", "true").strip().lower()
                       in ("1", "true", "yes", "on"))


def notifications_enabled() -> bool:
    """Read at call time so tests and reloads see the current value."""
    return bool(EMAIL_NOTIFICATIONS and RESEND_API_KEY and NOTIFY_EMAIL_TO.strip())


def notify_recipients() -> list[str]:
    return [a.strip() for a in NOTIFY_EMAIL_TO.split(",") if a.strip()]
