"""Application settings, read once from the environment (optionally via a local .env file).

Settings are a plain immutable object that the container passes to whatever needs it,
instead of modules reading global configuration themselves. Unsafe values are rejected
at startup rather than silently accepted.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIN_SECRET_LENGTH = 32

log = logging.getLogger("faultlines.settings")


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader, so the project needs no extra dependency for it."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: Path = ROOT / "faultlines.db"
    data_dir: Path = ROOT / "data"
    frontend_dir: Path = ROOT / "frontend"

    # Signs session tokens. A random key only means sessions do not survive a restart.
    secret_key: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)
    session_ttl_seconds: int = 8 * 3600
    password_iterations: int = 600_000          # OWASP 2023 guidance for PBKDF2-SHA256
    min_password_length: int = 10
    cookie_secure: bool = True                   # set COOKIE_SECURE=false only for plain-http hosts
    login_max_failures: int = 5
    login_window_seconds: int = 300
    rate_limit_requests: int = 300               # per client address
    rate_limit_window_seconds: int = 60

    # Demo mode lists the demo accounts on the sign-in screen. Off unless explicitly enabled.
    demo_mode: bool = False

    # Detection engine: "rules" (deterministic, offline) or "gemini" (Vertex AI).
    detection_engine: str = "rules"
    gcp_project: str = ""
    gcp_location: str = "europe-west1"
    gemini_model: str = "gemini-2.5-flash"
    pressure_window_days: int = 90

    demo_password: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if len(self.secret_key) < MIN_SECRET_LENGTH:
            raise ValueError(f"SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters")
        if self.detection_engine not in {"rules", "gemini"}:
            raise ValueError("DETECTION_ENGINE must be 'rules' or 'gemini'")


def load_settings(env_file: Path | None = ROOT / ".env") -> Settings:
    if env_file is not None:
        _load_dotenv(env_file)
    env = os.environ
    defaults = Settings()
    secret = env.get("SECRET_KEY") or ""
    if not secret:
        log.warning("SECRET_KEY is not set; using a random key, so sessions end when the server restarts.")
        secret = secrets.token_urlsafe(48)
    return Settings(
        db_path=Path(env.get("FAULTLINES_DB", str(defaults.db_path))),
        secret_key=secret,
        session_ttl_seconds=int(env.get("SESSION_TTL_SECONDS", defaults.session_ttl_seconds)),
        cookie_secure=_flag(env.get("COOKIE_SECURE"), defaults.cookie_secure),
        rate_limit_requests=int(env.get("RATE_LIMIT_REQUESTS", defaults.rate_limit_requests)),
        demo_mode=_flag(env.get("DEMO_MODE"), defaults.demo_mode),
        detection_engine=env.get("DETECTION_ENGINE", defaults.detection_engine).lower(),
        gcp_project=env.get("GOOGLE_CLOUD_PROJECT", ""),
        gcp_location=env.get("GOOGLE_CLOUD_LOCATION", defaults.gcp_location),
        gemini_model=env.get("GEMINI_MODEL", defaults.gemini_model),
        pressure_window_days=int(env.get("PRESSURE_WINDOW_DAYS", defaults.pressure_window_days)),
        demo_password=env.get("DEMO_PASSWORD") or None,
    )
