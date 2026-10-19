"""Graph transients must not fail a run, and a retry must never duplicate a write."""
from __future__ import annotations

import sys
import types
import unittest

import _paths  # noqa: F401
from shared import graph  # noqa: E402


class Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""


class FakeRequests(types.ModuleType):
    class ConnectionError(Exception):
        pass

    class Timeout(Exception):
        pass

    def __init__(self, replies):
        super().__init__("requests")
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Retry(unittest.TestCase):
    def send(self, replies, method, url):
        fake = FakeRequests(replies)
        slept = []
        real = sys.modules.get("requests")
        sys.modules["requests"] = fake
        try:
            resp = graph._send(method, url, sleep=slept.append)
        finally:
            if real is None:
                del sys.modules["requests"]
            else:
                sys.modules["requests"] = real
        return resp, fake.calls, slept

    def test_throttle_and_gateway_timeout_on_assign_are_retried(self):
        resp, calls, slept = self.send(
            [Resp(429, {"Retry-After": "7"}), Resp(504), Resp(200)],
            "POST", "https://graph/beta/deviceManagement/x/abc/assign")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(calls), 3)
        self.assertEqual(slept, [7, 4])

    def test_read_gives_up_after_the_last_attempt(self):
        resp, calls, _ = self.send([Resp(503)] * graph.ATTEMPTS, "GET", "https://graph/x")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(len(calls), graph.ATTEMPTS)

    def test_create_post_is_not_repeated_after_a_5xx(self):
        resp, calls, slept = self.send([Resp(502)], "POST", "https://graph/v1.0/groups")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual((len(calls), slept), (1, []))

    def test_create_post_is_retried_on_429(self):
        resp, calls, _ = self.send([Resp(429), Resp(201)], "POST", "https://graph/v1.0/groups")
        self.assertEqual((resp.status_code, len(calls)), (201, 2))

    def test_create_post_does_not_retry_a_dropped_connection(self):
        with self.assertRaises(FakeRequests.ConnectionError):
            self.send([FakeRequests.ConnectionError()], "POST", "https://graph/v1.0/groups")

    def test_patch_retries_a_dropped_connection(self):
        resp, calls, _ = self.send([FakeRequests.Timeout(), Resp(204)], "PATCH", "https://graph/x")
        self.assertEqual((resp.status_code, len(calls)), (204, 2))

    def test_client_errors_are_returned_at_once(self):
        resp, calls, slept = self.send([Resp(400)], "PATCH", "https://graph/x")
        self.assertEqual((resp.status_code, len(calls), slept), (400, 1, []))


if __name__ == "__main__":
    unittest.main()
