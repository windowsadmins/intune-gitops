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


def headers(content_type="text/csv", signature=None):
    h = {"Content-Type": content_type}
    if signature is not None:
        h["X-Signature-256"] = signature
    return h


class Check(unittest.TestCase):
    def test_valid_request_passes(self):
        self.assertIsNone(auth.check(headers(signature=auth.sign(BODY, SECRET)), BODY, SECRET))

    def test_charset_parameter_is_accepted(self):
        h = headers("text/csv; charset=utf-8", auth.sign(BODY, SECRET))
        self.assertIsNone(auth.check(h, BODY, SECRET))

    def test_fails_closed_without_a_secret(self):
        status, _ = auth.check(headers(signature=auth.sign(BODY, "")), BODY, "")
        self.assertEqual(status, 503)

    def test_missing_signature_is_401(self):
        self.assertEqual(auth.check(headers(), BODY, SECRET)[0], 401)

    def test_wrong_signature_is_401(self):
        self.assertEqual(auth.check(headers(signature=auth.sign(BODY, "other")), BODY, SECRET)[0], 401)

    def test_signature_over_a_different_body_is_401(self):
        sig = auth.sign(BODY, SECRET)
        self.assertEqual(auth.check(headers(signature=sig), BODY + b"x", SECRET)[0], 401)

    def test_browser_simple_content_types_are_415(self):
        sig = auth.sign(BODY, SECRET)
        for ctype in ("text/plain", "application/x-www-form-urlencoded",
                      "multipart/form-data; boundary=x", ""):
            with self.subTest(ctype=ctype):
                self.assertEqual(auth.check(headers(ctype, sig), BODY, SECRET)[0], 415)


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

    def post(self, target="intune", **kw):
        return self.client.post(f"/{target}", data=BODY, headers=headers(**kw))

    def test_signed_csv_reaches_the_consumer(self):
        resp = self.post(signature=auth.sign(BODY, SECRET))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.calls, [BODY.decode()])

    def test_unsigned_request_never_reaches_the_consumer(self):
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.calls, [])

    def test_form_post_never_reaches_the_consumer(self):
        resp = self.post(content_type="text/plain", signature=auth.sign(BODY, SECRET))
        self.assertEqual(resp.status_code, 415)
        self.assertEqual(self.calls, [])

    def test_unset_secret_refuses_everything(self):
        import os
        os.environ["WEBHOOK_SECRET"] = ""
        self.assertEqual(self.post(signature=auth.sign(BODY, SECRET)).status_code, 503)
        self.assertEqual(self.calls, [])

    def test_unknown_target_is_404_only_after_auth(self):
        self.assertEqual(self.post(target="nope").status_code, 401)
        self.assertEqual(self.post(target="nope", signature=auth.sign(BODY, SECRET)).status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
