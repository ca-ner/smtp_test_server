"""FastAPI application: REST API, static web UI, and the test-send endpoint."""

import asyncio
import logging
import os
import smtplib
from contextlib import asynccontextmanager
from email.message import EmailMessage

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .smtp import make_controller
from .store import store

log = logging.getLogger("api")

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")


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


app = FastAPI(title="Free SMTP Test Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- models --------------------------------------------------------------
class TestSend(BaseModel):
    sender: str = Field("tester@example.com", alias="from")
    to: str = "inbox@example.com"
    subject: str = "Test email"
    body: str = "Hello from the free SMTP test server!"

    class Config:
        populate_by_name = True


# -- API -----------------------------------------------------------------
@app.get("/api/info")
def info():
    return {
        "smtp_host": config.PUBLIC_SMTP_HOST,
        "smtp_port": config.SMTP_PORT,
        "auth": "none",
        "retention_hours": config.RETENTION_SECONDS // 3600,
        "max_per_address": config.MAX_PER_ADDRESS,
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
    msg = EmailMessage()
    msg["From"] = payload.sender
    msg["To"] = payload.to
    msg["Subject"] = payload.subject
    msg.set_content(payload.body)

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
