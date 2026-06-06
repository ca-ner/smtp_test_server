"""In-memory store for captured emails with expiry and per-address caps.

The store is intentionally simple and dependency-free so it can later be
swapped for a database-backed implementation behind the same interface.
"""

import threading
import time
import uuid
from collections import deque
from typing import Optional

from . import config


class EmailStore:
    def __init__(
        self,
        retention_seconds: int = config.RETENTION_SECONDS,
        max_per_address: int = config.MAX_PER_ADDRESS,
        max_total: int = config.MAX_TOTAL,
    ):
        self.retention_seconds = retention_seconds
        self.max_per_address = max_per_address
        self.max_total = max_total
        self._lock = threading.RLock()
        # newest appended on the right
        self._emails: deque = deque()
        self._by_id: dict = {}

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _addresses(email: dict) -> set:
        addrs = set()
        if email.get("from"):
            addrs.add(email["from"].lower())
        for t in email.get("to", []):
            if t:
                addrs.add(t.lower())
        return addrs

    def _expired(self, email: dict, now: float) -> bool:
        return (now - email["received_at"]) > self.retention_seconds

    def _summary(self, email: dict) -> dict:
        """A lightweight representation for list views (no raw/html body)."""
        return {
            "id": email["id"],
            "from": email["from"],
            "to": email["to"],
            "subject": email["subject"],
            "date": email["date"],
            "received_at": email["received_at"],
            "size": email["size"],
            "has_html": bool(email["html"]),
            "preview": (email["text"] or "")[:140],
        }

    # -- mutations -------------------------------------------------------
    def add(
        self,
        sender: str,
        recipients: list,
        subject: str,
        text: str,
        html: str,
        date: Optional[str],
        raw: str,
    ) -> dict:
        email = {
            "id": uuid.uuid4().hex,
            "from": sender or "",
            "to": recipients or [],
            "subject": subject or "(no subject)",
            "text": text or "",
            "html": html or "",
            "date": date or "",
            "received_at": time.time(),
            "raw": raw or "",
            "size": len(raw.encode("utf-8", "ignore")) if raw else 0,
        }
        with self._lock:
            self._emails.append(email)
            self._by_id[email["id"]] = email
            self._enforce_caps()
        return email

    def _enforce_caps(self):
        # per-address cap: keep newest N per address
        counts: dict = {}
        # iterate newest -> oldest
        to_drop = set()
        for email in reversed(self._emails):
            for addr in self._addresses(email):
                counts[addr] = counts.get(addr, 0) + 1
                if counts[addr] > self.max_per_address:
                    to_drop.add(email["id"])
        # global cap (drop oldest beyond max_total)
        overflow = len(self._emails) - self.max_total
        if overflow > 0:
            for email in list(self._emails)[:overflow]:
                to_drop.add(email["id"])
        if to_drop:
            self._remove_ids(to_drop)

    def _remove_ids(self, ids: set):
        if not ids:
            return
        self._emails = deque(e for e in self._emails if e["id"] not in ids)
        for i in ids:
            self._by_id.pop(i, None)

    def sweep(self) -> int:
        """Drop expired emails. Returns the number removed."""
        now = time.time()
        with self._lock:
            expired = {e["id"] for e in self._emails if self._expired(e, now)}
            self._remove_ids(expired)
            return len(expired)

    def clear(self) -> int:
        with self._lock:
            n = len(self._emails)
            self._emails.clear()
            self._by_id.clear()
            return n

    # -- reads -----------------------------------------------------------
    def recent(self, limit: int = 50) -> list:
        now = time.time()
        with self._lock:
            items = [
                self._summary(e)
                for e in reversed(self._emails)
                if not self._expired(e, now)
            ]
        return items[:limit]

    def query(self, address: str) -> list:
        addr = (address or "").strip().lower()
        if not addr:
            return self.recent()
        now = time.time()
        with self._lock:
            items = [
                self._summary(e)
                for e in reversed(self._emails)
                if not self._expired(e, now) and addr in self._addresses(e)
            ]
        return items[: self.max_per_address]

    def get(self, email_id: str) -> Optional[dict]:
        now = time.time()
        with self._lock:
            email = self._by_id.get(email_id)
            if not email or self._expired(email, now):
                return None
            return dict(email)

    def stats(self) -> dict:
        with self._lock:
            return {"total": len(self._emails)}


# module-level singleton
store = EmailStore()
