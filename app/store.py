"""In-memory store for captured emails with expiry and caps.

Kept dependency-free and behind a small interface so it can later be swapped
for a database-backed implementation.

Indexing notes:
- ``_emails`` is an ordered map id -> email (insertion order ~ time order,
  since ``received_at`` is monotonic), enabling O(1) front eviction and cheap
  expiry sweeps.
- ``_by_address`` maps a lowercased address -> ordered ids, so per-address
  lookups and the per-address cap are O(k) instead of scanning everything.
- ``_total_bytes`` tracks stored size so a global memory budget can be enforced.
"""

import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Optional

from . import config


class EmailStore:
    def __init__(
        self,
        retention_seconds: int = config.RETENTION_SECONDS,
        max_per_address: int = config.MAX_PER_ADDRESS,
        max_total: int = config.MAX_TOTAL,
        max_bytes: int = config.MAX_STORE_BYTES,
    ):
        self.retention_seconds = retention_seconds
        self.max_per_address = max_per_address
        self.max_total = max_total
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._emails: "OrderedDict[str, dict]" = OrderedDict()  # oldest first
        self._by_address: dict = {}  # addr -> deque[id] (oldest first)
        self._total_bytes = 0

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
        # approximate in-memory footprint: raw + decoded text + html
        email["_mem"] = email["size"] + len(email["text"]) + len(email["html"])
        with self._lock:
            self._emails[email["id"]] = email
            self._total_bytes += email["_mem"]
            for addr in self._addresses(email):
                self._by_address.setdefault(addr, deque()).append(email["id"])
            self._enforce_caps(email)
        return email

    def _evict(self, email_id: str):
        email = self._emails.pop(email_id, None)
        if email is None:
            return
        self._total_bytes -= email.get("_mem", 0)
        for addr in self._addresses(email):
            dq = self._by_address.get(addr)
            if dq:
                try:
                    dq.remove(email_id)
                except ValueError:
                    pass
                if not dq:
                    self._by_address.pop(addr, None)

    def _enforce_caps(self, new_email: dict):
        # per-address cap: evict oldest for any address over the limit
        for addr in self._addresses(new_email):
            dq = self._by_address.get(addr)
            while dq and len(dq) > self.max_per_address:
                self._evict(dq[0])
        # global count cap: drop oldest overall
        while len(self._emails) > self.max_total:
            self._evict(next(iter(self._emails)))
        # global byte budget: drop oldest until under budget
        while self._total_bytes > self.max_bytes and len(self._emails) > 1:
            self._evict(next(iter(self._emails)))

    def sweep(self) -> int:
        """Drop expired emails. Returns the number removed."""
        now = time.time()
        removed = 0
        with self._lock:
            # oldest first; stop at the first non-expired entry
            for email_id, email in list(self._emails.items()):
                if self._expired(email, now):
                    self._evict(email_id)
                    removed += 1
                else:
                    break
        return removed

    def clear(self) -> int:
        with self._lock:
            n = len(self._emails)
            self._emails.clear()
            self._by_address.clear()
            self._total_bytes = 0
            return n

    # -- reads -----------------------------------------------------------
    def recent(self, limit: int = 50) -> list:
        now = time.time()
        out = []
        with self._lock:
            for email in reversed(self._emails.values()):
                if self._expired(email, now):
                    continue
                out.append(self._summary(email))
                if len(out) >= limit:
                    break
        return out

    def query(self, address: str) -> list:
        addr = (address or "").strip().lower()
        if not addr:
            return self.recent()
        now = time.time()
        out = []
        with self._lock:
            dq = self._by_address.get(addr)
            if not dq:
                return []
            for email_id in reversed(dq):
                email = self._emails.get(email_id)
                if email and not self._expired(email, now):
                    out.append(self._summary(email))
                if len(out) >= self.max_per_address:
                    break
        return out

    def get(self, email_id: str) -> Optional[dict]:
        now = time.time()
        with self._lock:
            email = self._emails.get(email_id)
            if not email or self._expired(email, now):
                return None
            out = {k: v for k, v in email.items() if k != "_mem"}
            return out

    def stats(self) -> dict:
        with self._lock:
            return {"total": len(self._emails), "bytes": self._total_bytes}


# module-level singleton
store = EmailStore()
