"""Twilio Verify client tests (no network: httpx.MockTransport). Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import base64
import logging
import os
import unittest

import httpx

TWILIO_ENV = {"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "tok", "TWILIO_VERIFY_SERVICE_SID": "VA123"}


class _CollectHandler(logging.Handler):
    """Keeps app.sms warnings out of the test output, and lets a test read them back."""

    def __init__(self, sink: list[str]):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(record.getMessage())


class SmsTestBase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in TWILIO_ENV}
        os.environ.update(TWILIO_ENV)
        self.logged: list[str] = []
        self._logger = logging.getLogger("app.sms")
        self._handler = _CollectHandler(self.logged)
        self._logger.addHandler(self._handler)

    def tearDown(self):
        self._logger.removeHandler(self._handler)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def transport(self, status: int, payload: dict, seen: list | None = None) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen.append(request)
            return httpx.Response(status, json=payload)
        return httpx.MockTransport(handler)


class SmsStartTest(SmsTestBase):
    def test_posts_form_with_basic_auth(self):
        from app import sms
        seen: list[httpx.Request] = []
        self.assertIsNone(sms.start_verification("+13105551234", transport=self.transport(201, {"status": "pending"}, seen)))
        req = seen[0]
        self.assertEqual(str(req.url), "https://verify.twilio.com/v2/Services/VA123/Verifications")
        self.assertEqual(req.headers["authorization"], "Basic " + base64.b64encode(b"AC123:tok").decode())
        self.assertEqual(req.content, b"To=%2B13105551234&Channel=sms")

    def test_rate_limited(self):
        from app import sms
        with self.assertRaises(sms.SmsError) as cm:
            sms.start_verification("+13105551234", transport=self.transport(429, {"code": 60203}))
        self.assertEqual(str(cm.exception), "Too many codes sent to that number. Wait 10 minutes.")

    def test_invalid_number(self):
        from app import sms
        with self.assertRaises(sms.SmsError) as cm:
            sms.start_verification("+1310", transport=self.transport(400, {"code": 60200}))
        self.assertEqual(str(cm.exception), "That doesn't look like a valid mobile number.")

    def test_other_error(self):
        from app import sms
        with self.assertRaises(sms.SmsError) as cm:
            sms.start_verification("+13105551234", transport=self.transport(500, {}))
        self.assertEqual(str(cm.exception), "Couldn't send the text message. Try again in a minute.")


class SmsCheckTest(SmsTestBase):
    def test_approved(self):
        from app import sms
        seen: list[httpx.Request] = []
        self.assertTrue(sms.check_verification("+13105551234", "123456",
                                               transport=self.transport(200, {"status": "approved"}, seen)))
        self.assertEqual(str(seen[0].url), "https://verify.twilio.com/v2/Services/VA123/VerificationCheck")
        self.assertEqual(seen[0].content, b"To=%2B13105551234&Code=123456")

    def test_wrong_code_is_false(self):
        from app import sms
        self.assertFalse(sms.check_verification("+13105551234", "000000",
                                                transport=self.transport(200, {"status": "pending"})))

    def test_expired_verification_is_false(self):
        from app import sms
        self.assertFalse(sms.check_verification("+13105551234", "123456",
                                               transport=self.transport(404, {"code": 20404})))

    def test_other_error_raises(self):
        from app import sms
        with self.assertRaises(sms.SmsError) as cm:
            sms.check_verification("+13105551234", "123456", transport=self.transport(503, {}))
        self.assertEqual(str(cm.exception), "Couldn't check the code. Try again.")


class SmsLoggingTest(SmsTestBase):
    def test_warning_hides_the_subscriber_digits(self):
        from app import sms
        with self.assertRaises(sms.SmsError):
            sms.start_verification("+13105551234", transport=self.transport(500, {}))
        self.assertEqual(len(self.logged), 1)
        self.assertNotIn("5551234", self.logged[0])
        self.assertIn("+1310*******", self.logged[0])


class NormalizePhoneTest(unittest.TestCase):
    def test_accepts_the_shapes_people_type(self):
        from app.auth import normalize_phone
        for raw in ("3105551234", "(310) 555-1234", "310.555.1234", "13105551234", "1 310 555 1234",
                    "+1 (310) 555-1234", "+13105551234"):
            self.assertEqual(normalize_phone(raw), "+13105551234", raw)
        self.assertEqual(normalize_phone("+44 20 7946 0958"), "+442079460958")

    def test_rejects_junk(self):
        from app.api import ApiError
        from app.auth import normalize_phone
        for raw in ("", "   ", "555", "abc", "12345678901234567890", "+1234567", "5551234"):
            with self.assertRaises(ApiError, msg=raw) as cm:
                normalize_phone(raw)
            self.assertEqual(cm.exception.status, 400)
            self.assertEqual(cm.exception.message, "Enter a mobile number like (310) 555-1234.")


if __name__ == "__main__":
    unittest.main()
