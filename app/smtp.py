"""aiosmtpd handler: accept all mail, parse it, and store it.

Hardening:
- per-message size limit (advertised SIZE) and a low idle timeout (M2/H3)
- a process-wide concurrent-connection cap + per-IP connection/message rate
  limiting (M2/H3)
- optional STARTTLS when a cert/key are configured (M4)
- AUTH is not advertised on plaintext connections (L3)
"""

import logging
import ssl
import threading
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import getaddresses

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPProtocol

from . import config
from .ratelimit import RateLimiter
from .store import store

log = logging.getLogger("smtp")

# Per-IP rate limiters (sliding 60s windows).
_msg_limiter = RateLimiter(config.SMTP_MSGS_PER_MIN, 60)
_conn_limiter = RateLimiter(config.SMTP_CONNS_PER_MIN, 60)

# Process-wide concurrent connection accounting.
_conn_lock = threading.Lock()
_active_conns = 0


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
        peer_ip = session.peer[0] if session.peer else "unknown"
        if not _msg_limiter.allow(peer_ip):
            log.warning("Rate limit: too many messages from %s", peer_ip)
            return "452 Too many messages, slow down"
        try:
            raw_bytes = envelope.content
            if isinstance(raw_bytes, str):
                raw_bytes = raw_bytes.encode("utf-8", "replace")
            msg = message_from_bytes(raw_bytes)

            subject = _decode(msg.get("Subject"))
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


class LimitedSMTP(SMTPProtocol):
    """SMTP protocol with a concurrent-connection cap + per-IP conn limit."""

    def connection_made(self, transport):
        global _active_conns
        peer = transport.get_extra_info("peername")
        peer_ip = peer[0] if peer else "unknown"
        # per-IP connection rate limit
        if not _conn_limiter.allow(peer_ip):
            log.warning("Rate limit: too many connections from %s", peer_ip)
            self._refuse(transport, b"421 Too many connections, try later\r\n")
            return
        # process-wide concurrent cap
        with _conn_lock:
            if _active_conns >= config.SMTP_MAX_CONNECTIONS:
                log.warning("Connection cap reached (%d); refusing %s",
                            _active_conns, peer_ip)
                self._refuse(transport, b"421 Server busy, try later\r\n")
                return
            _active_conns += 1
            self._counted = True
        super().connection_made(transport)

    def connection_lost(self, exc):
        global _active_conns
        if getattr(self, "_counted", False):
            with _conn_lock:
                _active_conns = max(0, _active_conns - 1)
            self._counted = False
        super().connection_lost(exc)

    @staticmethod
    def _refuse(transport, message: bytes):
        try:
            transport.write(message)
        except Exception:
            pass
        finally:
            transport.close()


class CaptureController(Controller):
    """Controller that builds the hardened SMTP protocol."""

    def factory(self):
        return LimitedSMTP(self.handler, **self.SMTP_kwargs)


def _tls_context():
    if config.TLS_CERT_FILE and config.TLS_KEY_FILE:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(config.TLS_CERT_FILE, config.TLS_KEY_FILE)
        log.info("STARTTLS enabled (cert: %s)", config.TLS_CERT_FILE)
        return ctx
    return None


def make_controller(host: str, port: int) -> Controller:
    """Create (but do not start) a hardened aiosmtpd Controller."""
    return CaptureController(
        CaptureHandler(),
        hostname=host,
        port=port,
        # Capture-all test server: no auth. auth_require_tls=True keeps AUTH
        # off the EHLO banner on plaintext connections (L3).
        auth_required=False,
        auth_require_tls=True,
        # Resource limits.
        data_size_limit=config.MAX_MESSAGE_BYTES,
        timeout=config.SMTP_TIMEOUT,
        # Optional STARTTLS (M4); plaintext still allowed for testing.
        tls_context=_tls_context(),
        require_starttls=False,
    )
