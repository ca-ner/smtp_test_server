"""FastAPI application: REST API, static web UI, and the test-send endpoint.

Hardening:
- per-IP API rate limiting (H3/M2)
- input validation + size limits on test-send, with clean 4xx errors (M3)
- a Content-Security-Policy on app responses (L2)
- API docs disabled by default (L1)
"""

import asyncio
import logging
import os
import smtplib
from contextlib import asynccontextmanager
from email.message import EmailMessage

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .ratelimit import RateLimiter
from .smtp import make_controller
from .store import store

log = logging.getLogger("api")

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

_api_limiter = RateLimiter(config.API_REQS_PER_MIN, 60)

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; object-src 'none'; base-uri 'self'; "
    "frame-ancestors 'self'"
)


async def _sweeper():
    """Background task: periodically drop expired emails."""
    while True:
        try:
            await asyncio.sleep(config.SWEEP_INTERVAL_SECONDS)
            removed = store.sweep()
            if removed:
                log.info("Sweeper removed %d expired email(s)", removed)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("Sweeper error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    controller = make_controller(config.SMTP_HOST, config.SMTP_PORT)
    controller.start()
    log.info("SMTP capture server listening on %s:%s", config.SMTP_HOST, config.SMTP_PORT)
    sweeper = asyncio.create_task(_sweeper())
    try:
        yield
    finally:
        sweeper.cancel()
        controller.stop()
        log.info("SMTP capture server stopped")


app = FastAPI(
    title="Free SMTP Test Server",
    lifespan=lifespan,
    docs_url="/docs" if config.ENABLE_DOCS else None,
    redoc_url="/redoc" if config.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if config.ENABLE_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_and_headers(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if not _api_limiter.allow(client_ip):
        return JSONResponse(
            status_code=429, content={"detail": "Too many requests, slow down"}
        )
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# -- models --------------------------------------------------------------
class TestSend(BaseModel):
    sender: str = Field("tester@example.com", alias="from")
    to: str = "inbox@example.com"
    subject: str = "Test email"
    body: str = "Hello from the free SMTP test server!"

    class Config:
        populate_by_name = True


def _clean_header(value: str, field: str, max_len: int) -> str:
    """Reject CR/LF (header injection) and over-long values (M3)."""
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail=f"{field} is required")
    if "\n" in value or "\r" in value:
        raise HTTPException(status_code=422, detail=f"{field} may not contain newlines")
    if len(value) > max_len:
        raise HTTPException(status_code=422, detail=f"{field} is too long")
    return value


# -- API -----------------------------------------------------------------
@app.get("/api/info")
def info():
    return {
        "smtp_host": config.PUBLIC_SMTP_HOST,
        "smtp_port": config.SMTP_PORT,
        "auth": "none",
        "retention_hours": config.RETENTION_SECONDS // 3600,
        "max_per_address": config.MAX_PER_ADDRESS,
        "max_message_mb": round(config.MAX_MESSAGE_BYTES / (1024 * 1024), 1),
    }


@app.get("/api/emails")
def list_emails(address: str = ""):
    return {"emails": store.query(address), "address": address}


@app.get("/api/emails/{email_id}")
def get_email(email_id: str):
    email = store.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found or expired")
    return email


@app.delete("/api/emails")
def clear_emails():
    return {"cleared": store.clear()}


@app.post("/api/test-send")
async def test_send(payload: TestSend):
    sender = _clean_header(payload.sender, "from", config.MAX_ADDR_LEN)
    to = _clean_header(payload.to, "to", config.MAX_ADDR_LEN)
    subject = _clean_header(payload.subject, "subject", config.MAX_SUBJECT_LEN)
    body = payload.body or ""
    if len(body) > config.MAX_BODY_LEN:
        raise HTTPException(status_code=422, detail="body is too long")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    def _send():
        with smtplib.SMTP("127.0.0.1", config.SMTP_PORT, timeout=10) as s:
            s.send_message(msg)

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        log.exception("test-send failed")
        raise HTTPException(status_code=502, detail=f"Send failed: {exc}")
    return {"status": "sent"}


# -- static web UI -------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
