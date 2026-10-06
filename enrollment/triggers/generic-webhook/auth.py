"""Request checks for the generic webhook. No Flask import, so the test suite
exercises them without a web framework installed.

Every request must:
  * carry Content-Type text/csv. A browser can only send a cross-origin POST
    without a preflight as text/plain, form-urlencoded or multipart, so a page
    on another origin cannot reach a consumer through a user's browser.
  * carry X-Signature-Timestamp: <unix seconds>, within MAX_SKEW_SECONDS of
    this host's clock.
  * carry X-Signature-256: sha256=<hex HMAC-SHA256 of "<timestamp>." + raw
    body>, keyed with WEBHOOK_SECRET. The timestamp is inside the signed
    material, so a captured request stops working once it is five minutes old
    and its timestamp cannot be refreshed without the secret.

With WEBHOOK_SECRET unset the webhook refuses everything. It fails closed: a
consumer that rewrites group membership is not something to leave open because
a variable was forgotten.

The window bounds replay rather than eliminating it: inside those five minutes
a captured request can be resent, which is harmless here because every consumer
converges to the same state no matter how often it runs. A consumer that is not
idempotent would need a nonce store as well.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

SIGNATURE_HEADER = "X-Signature-256"
TIMESTAMP_HEADER = "X-Signature-Timestamp"
CONTENT_TYPE = "text/csv"
MAX_SKEW_SECONDS = 300


def sign(body: bytes, secret: str, timestamp: int | str) -> str:
    material = f"{timestamp}.".encode() + body
    return "sha256=" + hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()


def check(headers, body: bytes, secret: str | None = None,
          now: float | None = None) -> tuple[int, str] | None:
    """None if the request may proceed, else (status, reason)."""
    secret = os.getenv("WEBHOOK_SECRET", "") if secret is None else secret
    if not secret:
        return 503, "webhook disabled: WEBHOOK_SECRET is not set"

    media_type = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if media_type != CONTENT_TYPE:
        return 415, f"Content-Type must be {CONTENT_TYPE}"

    raw_ts = (headers.get(TIMESTAMP_HEADER) or "").strip()
    if not raw_ts.isdigit():
        return 401, f"missing or invalid {TIMESTAMP_HEADER}"
    now = time.time() if now is None else now
    if abs(now - int(raw_ts)) > MAX_SKEW_SECONDS:
        return 401, "request timestamp outside the allowed window"

    supplied = headers.get(SIGNATURE_HEADER) or ""
    if not hmac.compare_digest(supplied.encode(), sign(body, secret, raw_ts).encode()):
        return 401, "missing or invalid signature"
    return None
