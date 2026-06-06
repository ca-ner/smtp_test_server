"""aiosmtpd handler: accept all mail, parse it, and store it."""

import logging
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import getaddresses

from aiosmtpd.controller import Controller

from .store import store

log = logging.getLogger("smtp")


def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except (LookupError, TypeError):
        return payload.decode("utf-8", "replace")


def _extract_bodies(msg):
    text, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp.lower():
                continue
            if ctype == "text/plain" and not text:
                text = _part_text(part)
            elif ctype == "text/html" and not html:
                html = _part_text(part)
    else:
        if msg.get_content_type() == "text/html":
            html = _part_text(msg)
        else:
            text = _part_text(msg)
    return text, html


class CaptureHandler:
    """Accepts every recipient with no auth and stores the message."""

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        try:
            raw_bytes = envelope.content
            if isinstance(raw_bytes, str):
                raw_bytes = raw_bytes.encode("utf-8", "replace")
            msg = message_from_bytes(raw_bytes)

            subject = _decode(msg.get("Subject"))
            # Prefer envelope addresses (what the SMTP conversation used),
            # fall back to the header values.
            sender = envelope.mail_from or _decode(msg.get("From"))
            recipients = list(envelope.rcpt_tos)
            if not recipients:
                recipients = [a for _, a in getaddresses([msg.get("To", "")]) if a]

            text, html = _extract_bodies(msg)
            date = _decode(msg.get("Date"))

            email = store.add(
                sender=sender,
                recipients=recipients,
                subject=subject,
                text=text,
                html=html,
                date=date,
                raw=raw_bytes.decode("utf-8", "replace"),
            )
            log.info(
                "Captured email %s from=%s to=%s subject=%r",
                email["id"][:8], sender, recipients, subject,
            )
            return "250 Message accepted for delivery"
        except Exception as exc:  # never break the SMTP conversation
            log.exception("Failed to capture message: %s", exc)
            return "451 Internal error while storing message"


def make_controller(host: str, port: int) -> Controller:
    """Create (but do not start) an aiosmtpd Controller."""
    return Controller(
        CaptureHandler(),
        hostname=host,
        port=port,
        # No auth; this is a capture-all test server.
        auth_required=False,
        auth_require_tls=False,
    )
