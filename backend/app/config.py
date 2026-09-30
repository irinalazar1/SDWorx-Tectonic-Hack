"""Runtime configuration, read from environment variables (optionally via a local .env file)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"


def _load_dotenv() -> None:
    """Minimal .env loader so the project has no extra dependency for it."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DB_PATH = Path(os.environ.get("FAULTLINES_DB", str(ROOT / "faultlines.db")))

# Signing key for session tokens. A random key is used when none is configured,
# which simply means sessions do not survive a server restart.
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(48)
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", str(8 * 3600)))

# Detection engine: "rules" (deterministic, offline) or "gemini" (Vertex AI).
DETECTION_ENGINE = os.environ.get("DETECTION_ENGINE", "rules").lower()
GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GCP_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Pressure is measured over this window, ending at the latest date in the data.
PRESSURE_WINDOW_DAYS = int(os.environ.get("PRESSURE_WINDOW_DAYS", "90"))
