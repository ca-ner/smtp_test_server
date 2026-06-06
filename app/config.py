"""Environment-driven configuration."""

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# HTTP (web UI + API) server
HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = _int("HTTP_PORT", 8000)

# SMTP capture server. Default 2525 so it runs without root.
# Set SMTP_PORT=25 for a privileged production deploy.
SMTP_HOST = os.environ.get("SMTP_HOST", "0.0.0.0")
SMTP_PORT = _int("SMTP_PORT", 2525)

# Public hostname shown to users in the UI connection banner.
PUBLIC_SMTP_HOST = os.environ.get("PUBLIC_SMTP_HOST", "localhost")

# Retention: emails are dropped after this many seconds (default 48h).
RETENTION_SECONDS = _int("RETENTION_SECONDS", 48 * 3600)

# Keep at most this many emails per address (matches the original tool).
MAX_PER_ADDRESS = _int("MAX_PER_ADDRESS", 20)

# Hard cap on total emails held in memory (safety valve).
MAX_TOTAL = _int("MAX_TOTAL", 2000)

# How often the background sweeper runs (seconds).
SWEEP_INTERVAL_SECONDS = _int("SWEEP_INTERVAL_SECONDS", 300)
