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
| `app/smtp.py` | aiosmtpd handler: accept-all, parse, store |
| `app/store.py` | In-memory store with expiry + per-address caps |
| `app/api.py` | FastAPI: REST API, static UI, test-send, lifespan that starts SMTP + sweeper |
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
