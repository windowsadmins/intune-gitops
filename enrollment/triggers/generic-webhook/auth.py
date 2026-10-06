"""Request checks for the generic webhook. No Flask import, so the test suite
exercises them without a web framework installed.

Every request must:
  * carry Content-Type text/csv. A browser can only send a cross-origin POST
    without a preflight as text/plain, form-urlencoded or multipart, so a page
    on another origin cannot reach a consumer through a user's browser.
  * carry X-Signature-256: sha256=<hex HMAC-SHA256 of the raw body>, keyed with
    WEBHOOK_SECRET -- the same shape GitHub uses for its webhooks.

With WEBHOOK_SECRET unset the webhook refuses everything. It fails closed: a
consumer that rewrites group membership is not something to leave open because
a variable was forgotten.
"""
from __future__ import annotations

import hashlib
import hmac
import os

SIGNATURE_HEADER = "X-Signature-256"
CONTENT_TYPE = "text/csv"


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def check(headers, body: bytes, secret: str | None = None) -> tuple[int, str] | None:
    """None if the request may proceed, else (status, reason)."""
    secret = os.getenv("WEBHOOK_SECRET", "") if secret is None else secret
    if not secret:
        return 503, "webhook disabled: WEBHOOK_SECRET is not set"

    media_type = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if media_type != CONTENT_TYPE:
        return 415, f"Content-Type must be {CONTENT_TYPE}"

    supplied = headers.get(SIGNATURE_HEADER) or ""
    if not hmac.compare_digest(supplied.encode(), sign(body, secret).encode()):
        return 401, "missing or invalid signature"
    return None
