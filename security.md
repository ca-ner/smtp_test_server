# Security & Scalability Assessment

**Target:** Free SMTP Test Server (capture-all)
**Scenario tested:** public service used by ~100 concurrent users
**Date:** 2026-06-06
**Method:** static code review + **dynamic testing against a running instance**
(attacks + a 100-concurrent-user load test). All findings below were reproduced
unless marked *(static)*.

---

## Executive summary

The service works and handles 100 concurrent users comfortably from a *throughput*
standpoint (100 concurrent SMTP sends + 100 concurrent API reads completed in
0.32 s, 0 errors, p50 6–17 ms). The risks are not throughput — they are
**multi-tenancy, abuse resistance, and resource exhaustion**:

- There is **no authentication and a single global inbox**, so any user can read
  every other user's captured mail (including secrets like password-reset tokens)
  and a single unauthenticated request can **wipe all 100 users' data**.
- The server will **accept 32 MB messages with no rate limiting** and stores each
  message multiple times in RAM, making memory exhaustion trivial.
- The design **cannot scale horizontally** (in-memory state + a single SMTP port
  bind); a second worker/instance crashes on startup, and any restart loses all mail.

Severity counts: **3 High, 4 Medium, 4 Low/Info.**

| # | Severity | Finding |
|---|----------|---------|
| H1 | High | No auth + global shared inbox → anyone reads anyone's mail |
| H2 | High | Unauthenticated global `DELETE` wipes all users (amplified by CORS `*`) |
| H3 | High | Memory-exhaustion DoS (32 MB msgs, stored ~2–3×, no rate limit) |
| M1 | Medium | No horizontal scalability; single instance is a hard ceiling & SPOF |
| M2 | Medium | SMTP connection exhaustion / slowloris (300 s idle, no conn cap) |
| M3 | Medium | Unhandled exception → HTTP 500 on crafted input; no API input limits |
| M4 | Medium | No transport encryption (STARTTLS disabled); plaintext secrets |
| L1 | Low | FastAPI `/docs`, `/redoc`, `/openapi.json` publicly exposed |
| L2 | Low | HTML email residual risks (tracking pixels load; no CSP) |
| L3 | Low | `AUTH` advertised although optional/unenforced (misleading) |
| L4 | Info | `O(n)` cap enforcement per insert → CPU cliff as store fills *(static)* |

---

## High severity

### H1 — No authentication + global shared inbox (confidentiality)
**What:** Every captured email lands in one global store. `GET /api/emails?address=`
returns any address's mail to anyone, with no auth. At 100 users this is one
shared mailbox, not 100 private ones.

**Reproduced:**
```
POST /api/test-send {to: "alice@corp.com", subject: "Password reset token=ABC123"}
GET  /api/emails?address=alice@corp.com   (as an unrelated client)
  → leaked subject: "Password reset token=ABC123"
```
**Impact at scale:** Real apps under test routinely send password resets, magic
links, and OTPs through SMTP. Any user (or any script) can read another user's
tokens by guessing/knowing the address. UUID message IDs are not enumerable, but
addresses are, so this protects nothing.

**Remediation (pick per threat model):**
- Scope inboxes by an unguessable token: issue a random "inbox id" and key
  lookups on it (`/api/emails?box=<token>`), instead of by raw email address.
- Or require a per-session key / basic auth in front of the API.
- At minimum, document loudly that the inbox is public and must not receive real
  secrets. Files: `app/api.py` (`list_emails`, `get_email`), `app/store.py`.

### H2 — Unauthenticated global delete + CORS wildcard (integrity/availability)
**What:** `DELETE /api/emails` clears the **entire** store with no auth, and
`CORSMiddleware(allow_origins=["*"])` lets **any website** call it from a victim's
browser.

**Reproduced:**
```
(seed 1 email)  GET /api/emails → count=1
DELETE /api/emails   (no auth)  → {"cleared": 1}
                GET /api/emails → count=0
```
**Impact at scale:** One request — or a hidden `fetch()` on any page a user
visits — destroys all 100 users' captured mail. The wildcard CORS turns this into
a drive-by CSRF and also allows cross-origin *exfiltration* of H1's data.

**Remediation:** Remove the global `DELETE` or scope it to the caller's own inbox
(see H1's box token); require auth for destructive ops; replace `allow_origins=["*"]`
with an explicit allow-list (or drop CORS entirely if the UI is same-origin, which
it is). File: `app/api.py` (`clear_emails`, `add_middleware`).

### H3 — Memory-exhaustion DoS (availability)
**What:** SMTP advertises `SIZE 33554432` (32 MB default, never overridden). Each
message is stored as `raw` **plus** decoded `text` **plus** `html`, so RAM use is
~2–3× the wire size. `MAX_TOTAL=2000` caps *count*, not *bytes* → up to ~64 GB
theoretical. There is **no rate limiting** on SMTP or the API.

**Reproduced:** a single 20 MB message was accepted in 2.2 s and pushed process
RSS from ~30 MB to **137 MB**. Multiply by concurrent senders / 2000-message cap.

**Impact at scale:** A handful of large messages, or a loop of medium ones from
any of the 100 users, OOM-kills the single process and takes the service down for
everyone.

**Remediation:**
- Set a small `data_size_limit` on the SMTP server (e.g. 1–5 MB) in
  `app/smtp.py` (`make_controller`, pass `data_size_limit=`).
- Add a **global byte budget** to `EmailStore` (evict oldest when exceeded), not
  just a count cap. File: `app/store.py` (`_enforce_caps`, new `max_bytes`).
- Avoid storing `raw` + `text` + `html` in full; store `raw` once and derive views,
  or cap stored body length.
- Add per-IP rate limiting / connection throttling (see M2).

---

## Medium severity

### M1 — No horizontal scalability; single instance is a SPOF
**What:** State lives in process memory and the SMTP listener binds one port.
Running a second worker/instance to handle load fails immediately.

**Reproduced:** starting a second instance on the same SMTP port:
```
OSError: [Errno 98] error while attempting to bind on address
('0.0.0.0', 2525): address already in use
```
So `uvicorn --workers >1` or any 2nd replica crashes, and even if it didn't, each
worker would have a *separate* inbox (reads wouldn't see mail captured by another
worker). A restart/crash also loses all stored mail.

**Impact at scale:** 100 users are served by exactly one process on one box. No
redundancy, no load-balancing, no zero-downtime deploys.

**Remediation:** Move shared state to an external store (Redis/SQLite/Postgres)
so the API can run multiple stateless workers behind a load balancer, while the
SMTP listener runs as its own single deployable writing to the same store. The
plan already isolates `EmailStore` behind a small interface, which makes this a
contained change. Files: `app/store.py` (swap implementation), `app/api.py`,
`app/smtp.py`.

### M2 — SMTP connection exhaustion / slowloris
**What:** aiosmtpd idle `timeout` defaults to **300 s** and there is no
max-connection cap or per-IP limit. An attacker can open many connections and
hold each open doing nothing.

**Reproduced *(config-confirmed)*:** `SMTP.timeout = 300`; no connection limit is
configured in `make_controller`.

**Impact at scale:** Cheap to exhaust the single event loop / file descriptors and
starve the 100 legitimate users.

**Remediation:** Lower `timeout` (e.g. 30–60 s), cap concurrent connections, and
add per-IP rate limiting (a reverse proxy / firewall in front, or a small
semaphore + connection accounting in the handler). File: `app/smtp.py`.

### M3 — Unhandled exception (HTTP 500) on crafted input; no API input limits
**What:** `POST /api/test-send` builds an `EmailMessage` directly from user input.
Header values containing CR/LF make Python's email library raise, which is **not
caught**, returning a 500 and a stack trace in logs.

**Reproduced:**
```
POST /api/test-send {to: "b@b.com\r\nBcc: hidden@evil.com", ...}
  → HTTP 500 Internal Server Error
  → ValueError: Header values may not contain linefeed or carriage return
```
Good news: Python **blocks** the header-injection itself. Bad news: the
unvalidated path crashes instead of returning a clean 4xx, and there are no
length limits on `subject`/`body` (ties into H3).

**Remediation:** Validate inputs with Pydantic (email format, max lengths), strip
CR/LF, and wrap message construction in try/except returning `400`. File:
`app/api.py` (`TestSend` model, `test_send`).

### M4 — No transport encryption (STARTTLS disabled)
**What:** `EHLO` does not offer `STARTTLS`; all SMTP traffic is plaintext.
**Reproduced:** `STARTTLS offered: False`; capabilities = `SIZE, 8BITMIME, SMTPUTF8,
AUTH LOGIN PLAIN, HELP`.
**Impact:** Anything sent (including the password-reset tokens from H1) is readable
on the wire. Acceptable for a localhost-only test tool, **not** for a public service.
**Remediation:** Offer STARTTLS with a certificate for any non-local deployment;
serve the web UI over HTTPS (terminate TLS at a reverse proxy). File: `app/smtp.py`.

---

## Low / Informational

### L1 — API documentation publicly exposed
`/docs`, `/redoc`, `/openapi.json` all return **200**. Harmless on its own but it
advertises the destructive endpoints (H2) to anyone. Disable in production via
`FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`. File: `app/api.py`.

### L2 — HTML email rendering: residual risks (active XSS mitigated)
The frontend escapes all header fields with `esc()` and renders HTML bodies in an
`<iframe sandbox="">` (most-restrictive: no scripts, no same-origin), so stored
`<script>`/`onerror` payloads do **not** execute — verified the payload is stored
verbatim but rendered inertly. Residual: `sandbox=""` does **not** block network
loads, so tracking pixels / remote images in an email still fire (deanonymization);
and the app pages have **no Content-Security-Policy**. Remediation: add a CSP
header, and consider blocking remote content in the iframe (`sandbox` + CSP
`img-src 'none'` inside `srcdoc`, or a proxy). Files: `web/app.js`, `app/api.py`.

### L3 — `AUTH` advertised though optional/unenforced
`EHLO` lists `AUTH LOGIN PLAIN` even though auth is not required and no
authenticator is configured. Misleading to clients; set `auth_required=False`
*and* avoid advertising AUTH (or wire a real authenticator). File: `app/smtp.py`.

### L4 — `O(n)` cap enforcement per insert *(static)*
`EmailStore._enforce_caps()` scans all stored emails (and rebuilds the deque on
eviction) on **every** insert. Fine at the tested load, but as the store fills
toward `MAX_TOTAL` under sustained sending this becomes an `O(n)` cost per message
held under a global lock — a gradual CPU/contention cliff. Remediation: maintain
per-address counts incrementally; use the deque's natural ordering for global
eviction. File: `app/store.py`.

---

## What's working well
- **Thread-safety held under concurrency:** SMTP runs in its own event-loop thread
  while the API runs in another; the `threading.RLock` in `EmailStore` kept the
  100-concurrent test at **0 errors**.
- **Throughput at 100 users is fine:** 200 concurrent ops in 0.32 s, p50 6–17 ms.
- **Active stored-XSS is mitigated** (escaping + sandboxed iframe) — see L2.
- **Header injection is blocked** by Python's email policy — see M3.
- **Message IDs are unguessable** (UUID4), so direct-object enumeration isn't viable.
- Caps & expiry (20/address, 48 h) behave as designed and were verified earlier.

## Load test configuration (for reproducibility)
- 100 concurrent SMTP sends (`smtplib`, 500-byte bodies) + 100 concurrent
  `GET /api/emails` reads, fired via a thread pool driven by `asyncio.gather`.
- Single instance, defaults (`HTTP_PORT=8000`, `SMTP_PORT=2525`), one uvicorn worker.
- Large-message test: one 20 MB message; RSS sampled via `ps -o rss`.

## Prioritized remediation roadmap
1. **H1/H2 (multi-tenancy):** unguessable per-inbox tokens; scope reads & deletes;
   tighten CORS; remove/scope global `DELETE`.
2. **H3/M2 (abuse resistance):** cap message size, add a store byte-budget, add
   rate limiting / connection caps.
3. **M1 (scalability):** externalize state (Redis/DB) so the API scales to multiple
   workers and survives restarts; run SMTP as its own deployable.
4. **M3/M4/L1–L4 (hardening):** validate inputs, disable public docs, add CSP/HTTPS,
   STARTTLS, and fix the `O(n)` insert path.
