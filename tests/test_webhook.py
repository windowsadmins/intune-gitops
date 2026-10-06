#!/usr/bin/env python3
"""The generic webhook refuses anything it cannot authenticate.

A consumer rewrites group membership across the fleet, so the front door fails
closed: no secret, no service; wrong media type or bad signature, no call.
"""
from __future__ import annotations

import unittest

import _paths  # noqa: F401
import auth
from consumers import registry

SECRET = "test-secret"
BODY = b"serial,usage,catalog,area,location,status\nSAMPLE001,Assigned,Staff,IT,B1101,Active\n"


NOW = 1_800_000_000


def headers(content_type="text/csv", signature=None, timestamp=NOW):
    h = {"Content-Type": content_type}
    if timestamp is not None:
        h["X-Signature-Timestamp"] = str(timestamp)
    if signature is not None:
        h["X-Signature-256"] = signature
    return h


def signed(body=BODY, secret=SECRET, timestamp=NOW, **kw):
    return headers(signature=auth.sign(body, secret, timestamp), timestamp=timestamp, **kw)


class Check(unittest.TestCase):
    def check(self, h, body=BODY, secret=SECRET, now=NOW):
        return auth.check(h, body, secret, now=now)

    def test_valid_request_passes(self):
        self.assertIsNone(self.check(signed()))

    def test_charset_parameter_is_accepted(self):
        self.assertIsNone(self.check(signed(content_type="text/csv; charset=utf-8")))

    def test_fails_closed_without_a_secret(self):
        self.assertEqual(self.check(signed(secret=""), secret="")[0], 503)

    def test_missing_signature_is_401(self):
        self.assertEqual(self.check(headers())[0], 401)

    def test_wrong_signature_is_401(self):
        self.assertEqual(self.check(signed(secret="other"))[0], 401)

    def test_signature_over_a_different_body_is_401(self):
        self.assertEqual(self.check(signed(), body=BODY + b"x")[0], 401)

    def test_browser_simple_content_types_are_415(self):
        for ctype in ("text/plain", "application/x-www-form-urlencoded",
                      "multipart/form-data; boundary=x", ""):
            with self.subTest(ctype=ctype):
                self.assertEqual(self.check(signed(content_type=ctype))[0], 415)

    def test_missing_or_garbled_timestamp_is_401(self):
        sig = auth.sign(BODY, SECRET, NOW)
        for ts in (None, "", "soon", "-5", "1.5"):
            with self.subTest(ts=ts):
                self.assertEqual(self.check(headers(signature=sig, timestamp=ts))[0], 401)

    def test_replay_after_the_window_is_401(self):
        h = signed()
        self.assertIsNone(self.check(h, now=NOW + auth.MAX_SKEW_SECONDS))
        self.assertEqual(self.check(h, now=NOW + auth.MAX_SKEW_SECONDS + 1)[0], 401)

    def test_future_timestamp_outside_the_window_is_401(self):
        self.assertEqual(self.check(signed(), now=NOW - auth.MAX_SKEW_SECONDS - 1)[0], 401)

    def test_timestamp_cannot_be_refreshed_without_the_secret(self):
        """Swapping a fresh timestamp onto a captured signature breaks it."""
        stale = signed(timestamp=NOW - 3600)
        stale["X-Signature-Timestamp"] = str(NOW)
        self.assertEqual(self.check(stale)[0], 401)


class Registry(unittest.TestCase):
    def test_intune_is_built_in(self):
        self.assertIn("intune", registry.load(""))

    def test_client_consumers_are_registered_by_name(self):
        table = registry.load("echo=json:dumps")
        self.assertIn("echo", table)

    def test_bad_entry_is_an_error(self):
        with self.assertRaises(ValueError):
            registry.load("nonsense")


try:
    import flask  # noqa: F401
    HAVE_FLASK = True
except ImportError:
    HAVE_FLASK = False


@unittest.skipUnless(HAVE_FLASK, "flask not installed")
class App(unittest.TestCase):
    def setUp(self):
        import os
        import app as webhook
        self.calls = []
        os.environ["WEBHOOK_SECRET"] = SECRET
        self.addCleanup(os.environ.pop, "WEBHOOK_SECRET", None)
        consumers = {"intune": lambda text: self.calls.append(text) or 0}
        self.client = webhook.create_app(consumers).test_client()

    def post(self, target="intune", sign=True, **kw):
        import time
        now = int(time.time())
        h = signed(timestamp=now, **kw) if sign else headers(timestamp=now, **kw)
        return self.client.post(f"/{target}", data=BODY, headers=h)

    def test_signed_csv_reaches_the_consumer(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.calls, [BODY.decode()])

    def test_unsigned_request_never_reaches_the_consumer(self):
        self.assertEqual(self.post(sign=False).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_form_post_never_reaches_the_consumer(self):
        resp = self.post(content_type="text/plain")
        self.assertEqual(resp.status_code, 415)
        self.assertEqual(self.calls, [])

    def test_unset_secret_refuses_everything(self):
        import os
        os.environ["WEBHOOK_SECRET"] = ""
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.calls, [])

    def test_unknown_target_is_404_only_after_auth(self):
        self.assertEqual(self.post(target="nope", sign=False).status_code, 401)
        self.assertEqual(self.post(target="nope").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
