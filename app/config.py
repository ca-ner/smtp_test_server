"""Environment-driven configuration."""

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


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

# --- Resource limits (H3) ------------------------------------------------
# Max accepted SMTP message size in bytes (advertised as SIZE). Default 2 MB
# so a single message cannot blow up memory; raise if you test large mail.
MAX_MESSAGE_BYTES = _int("MAX_MESSAGE_BYTES", 2 * 1024 * 1024)

# Global memory budget for stored emails (bytes). Oldest are evicted when
# exceeded, so total RAM for the store is bounded regardless of count.
MAX_STORE_BYTES = _int("MAX_STORE_BYTES", 128 * 1024 * 1024)

# --- SMTP connection hardening (M2) -------------------------------------
# Idle timeout per SMTP connection (seconds). Low value shrinks the
# slowloris window.
SMTP_TIMEOUT = _int("SMTP_TIMEOUT", 30)

# Max concurrent SMTP connections (process-wide). Excess are refused (421).
SMTP_MAX_CONNECTIONS = _int("SMTP_MAX_CONNECTIONS", 200)

# Per-IP rate limits (sliding 60s window).
SMTP_MSGS_PER_MIN = _int("SMTP_MSGS_PER_MIN", 100)
SMTP_CONNS_PER_MIN = _int("SMTP_CONNS_PER_MIN", 200)
API_REQS_PER_MIN = _int("API_REQS_PER_MIN", 600)

# --- test-send input limits (M3) ----------------------------------------
MAX_SUBJECT_LEN = _int("MAX_SUBJECT_LEN", 998)
MAX_ADDR_LEN = _int("MAX_ADDR_LEN", 320)
MAX_BODY_LEN = _int("MAX_BODY_LEN", 100 * 1024)

# --- Hardening toggles --------------------------------------------------
# Expose FastAPI's /docs, /redoc, /openapi.json (L1). Off by default.
ENABLE_DOCS = _bool("ENABLE_DOCS", False)

# Optional STARTTLS (M4): set both to a PEM cert and key to enable. When
# unset (default, e.g. local testing) the server stays plaintext.
TLS_CERT_FILE = os.environ.get("TLS_CERT_FILE", "")
TLS_KEY_FILE = os.environ.get("TLS_KEY_FILE", "")
