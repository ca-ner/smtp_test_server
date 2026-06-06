# 📬 Free SMTP Test Server

A self-hostable, **capture-all SMTP server** with a modern web UI — a clean
replica of tools like WPOven's "Free SMTP Server for Testing", MailHog, and
Mailtrap.

Point any application's email/SMTP settings at this server. Every message it
receives is **accepted with no authentication**, parsed, and shown in the web
inbox where you can look it up by sender or recipient address. Nothing is ever
delivered to the real internet — it's a safe sink for testing.

## Features

- **Real SMTP listener** (via `aiosmtpd`) that accepts any mail, no auth required.
- **Modern web UI** — address lookup, email list, and a reader with Text / HTML
  (sandboxed) / Raw tabs. Responsive, with light/dark themes.
- **Send a test email** straight from the UI to see the capture flow instantly.
- **In-memory storage** with automatic expiry: last 20 emails per address,
  deleted after 48 hours (both configurable).
- **Single process**, minimal dependencies, runs anywhere. Dockerfile included.

## Quick start (local)

```bash
pip install -r requirements.txt
python -m app.main
```

Then open <http://localhost:8000>.

- Web UI / API: `http://localhost:8000`
- SMTP capture: `localhost:2525` (no auth)

Send a quick test from another terminal:

```bash
python - <<'PY'
import smtplib
from email.message import EmailMessage
m = EmailMessage()
m["From"], m["To"], m["Subject"] = "me@example.com", "you@example.com", "Hi"
m.set_content("Hello capture-all!")
with smtplib.SMTP("localhost", 2525) as s:
    s.send_message(m)
PY
```

…or with [swaks](https://github.com/jetmore/swaks):

```bash
swaks --server localhost:2525 --to you@example.com --from me@example.com
```

The message appears in the inbox within a few seconds.

## Run with Docker

```bash
docker build -t smtp-test-server .
docker run -p 8000:8000 -p 2525:2525 smtp-test-server
```

## Configuration

All settings are environment variables (with sensible defaults):

| Variable | Default | Description |
|---|---|---|
| `HTTP_HOST` | `0.0.0.0` | Web UI / API bind address |
| `HTTP_PORT` | `8000` | Web UI / API port |
| `SMTP_HOST` | `0.0.0.0` | SMTP listener bind address |
| `SMTP_PORT` | `2525` | SMTP listener port. Use `25` for a privileged production deploy (requires root/capabilities). |
| `PUBLIC_SMTP_HOST` | `localhost` | Hostname shown to users in the UI connection banner |
| `RETENTION_SECONDS` | `172800` | How long captured emails are kept (48h) |
| `MAX_PER_ADDRESS` | `20` | Max emails kept per address |
| `MAX_TOTAL` | `2000` | Global safety cap on stored emails |
| `SWEEP_INTERVAL_SECONDS` | `300` | How often expired emails are purged |
| `MAX_MESSAGE_BYTES` | `2097152` | Max accepted SMTP message size (2 MB; advertised as `SIZE`) |
| `MAX_STORE_BYTES` | `134217728` | Global memory budget for stored mail (128 MB); oldest evicted when exceeded |
| `SMTP_TIMEOUT` | `30` | Idle timeout per SMTP connection (seconds) |
| `SMTP_MAX_CONNECTIONS` | `200` | Max concurrent SMTP connections (excess refused `421`) |
| `SMTP_MSGS_PER_MIN` | `100` | Per-IP SMTP message rate limit |
| `SMTP_CONNS_PER_MIN` | `200` | Per-IP SMTP connection rate limit |
| `API_REQS_PER_MIN` | `600` | Per-IP API request rate limit |
| `ENABLE_DOCS` | `false` | Expose FastAPI `/docs`, `/redoc`, `/openapi.json` |
| `TLS_CERT_FILE` / `TLS_KEY_FILE` | _(unset)_ | PEM cert + key to enable STARTTLS (plaintext if unset) |

### Security hardening

This server was assessed for security & scalability — see
[`security.md`](security.md). Implemented hardening (all on by default):
per-message size limit + global memory budget, per-IP rate limiting (SMTP & API),
a concurrent-connection cap and low idle timeout, input validation on `test-send`,
a Content-Security-Policy (app responses **and** the email-viewer iframe, which
blocks remote tracking pixels), API docs disabled, and AUTH not advertised on
plaintext. Optional STARTTLS via `TLS_CERT_FILE`/`TLS_KEY_FILE`.

Three findings are intentionally **accepted** because this is a disposable test
environment: the inbox is public/unauthenticated (don't send real secrets), the
inbox can be cleared by anyone, and it runs as a single instance. See `security.md`
for what to change before any production use.

## Deployment notes

A capture-all SMTP server needs a long-running process with a **public inbound
TCP port** for SMTP. That rules out static/CI-only hosts (e.g. GitHub Pages or
GitHub Actions). Run it on any machine/VPS that can expose the SMTP and HTTP
ports — directly, via the Docker image, or behind a reverse proxy for the HTTP
side. To listen on the standard SMTP port 25, either run with privileges/
`CAP_NET_BIND_SERVICE`, or keep `SMTP_PORT=2525` and map it (`-p 25:2525`).

## Architecture

| File | Purpose |
|---|---|
| `app/smtp.py` | aiosmtpd handler: accept-all, parse, store; size/timeout/rate/conn limits; optional STARTTLS |
| `app/store.py` | Indexed in-memory store: expiry, per-address caps, global byte budget |
| `app/api.py` | FastAPI: REST API, static UI, test-send, rate-limit + CSP middleware, lifespan |
| `app/ratelimit.py` | Tiny thread-safe per-key sliding-window rate limiter |
| `app/config.py` | Env-driven configuration |
| `app/main.py` | Entrypoint (uvicorn) |
| `web/` | Modern vanilla HTML/CSS/JS frontend |

The storage layer is isolated behind a small interface, so a database-backed
implementation can be dropped in later without touching the SMTP or API code.

## API

- `GET /api/info` — connection settings shown in the UI
- `GET /api/emails?address=<addr>` — list captured emails (all if `address` empty)
- `GET /api/emails/{id}` — full email (text, html, raw)
- `POST /api/test-send` — `{ "from", "to", "subject", "body" }`, injects a message
- `DELETE /api/emails` — clear the inbox
