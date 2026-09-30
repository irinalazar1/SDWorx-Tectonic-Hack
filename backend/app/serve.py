"""Run the server with safe defaults.

Usage:  python -m backend.app.serve
Binds to 127.0.0.1 unless HOST is set, hides the server banner, and ignores
X-Forwarded-* headers unless you run behind a trusted proxy (TRUST_PROXY=true),
so clients cannot spoof their address to dodge rate limits.
"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    trust_proxy = os.environ.get("TRUST_PROXY", "").lower() in {"1", "true", "yes"}
    uvicorn.run(
        "backend.app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        server_header=False,
        date_header=False,
        proxy_headers=trust_proxy,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1") if trust_proxy else None,
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
