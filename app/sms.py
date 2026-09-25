"""Sign-in codes by text message, via the Twilio Verify API.

Pass `transport` to point the httpx client at a fake in tests (same pattern as app/ai.py: call_model).
"""
from __future__ import annotations

import logging

import httpx

from .config import get_config

BASE_URL = "https://verify.twilio.com/v2/Services"
TIMEOUT = 10
log = logging.getLogger(__name__)


class SmsError(Exception):
    """An error whose message is safe to show on the login screen."""


def _redact(phone: str) -> str:
    """Never log the last 7 digits of a phone number."""
    return phone[:-7] + "*******" if len(phone) > 7 else "*******"


def _post(path: str, data: dict, transport: httpx.BaseTransport | None) -> httpx.Response:
    cfg = get_config()
    url = f"{BASE_URL}/{cfg.twilio_verify_service_sid}/{path}"
    with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
        return client.post(url, data=data, auth=(cfg.twilio_account_sid, cfg.twilio_auth_token))


def _twilio_code(resp: httpx.Response) -> int | None:
    try:
        v = resp.json().get("code")
    except ValueError:
        return None
    return v if isinstance(v, int) else None


def start_verification(phone: str, transport: httpx.BaseTransport | None = None) -> None:
    """Ask Twilio to text a code to `phone`. Raises SmsError with a readable message."""
    try:
        resp = _post("Verifications", {"To": phone, "Channel": "sms"}, transport)
    except httpx.HTTPError as e:
        log.warning("Twilio Verify start failed for %s: %s", _redact(phone), e)
        raise SmsError("Couldn't send the text message. Try again in a minute.")
    if resp.status_code == 201:
        return
    code = _twilio_code(resp)
    log.warning("Twilio Verify start for %s returned HTTP %s (code %s)", _redact(phone), resp.status_code, code)
    if resp.status_code == 429 or code == 60203:
        raise SmsError("Too many codes sent to that number. Wait 10 minutes.")
    if resp.status_code == 400 or code == 60200:
        raise SmsError("That doesn't look like a valid mobile number.")
    raise SmsError("Couldn't send the text message. Try again in a minute.")


def check_verification(phone: str, code: str, transport: httpx.BaseTransport | None = None) -> bool:
    """True when Twilio approves the code. False when it is wrong, expired or already used."""
    try:
        resp = _post("VerificationCheck", {"To": phone, "Code": code}, transport)
    except httpx.HTTPError as e:
        log.warning("Twilio Verify check failed for %s: %s", _redact(phone), e)
        raise SmsError("Couldn't check the code. Try again.")
    if resp.status_code == 200:
        try:
            return resp.json().get("status") == "approved"
        except ValueError:
            raise SmsError("Couldn't check the code. Try again.")
    if resp.status_code == 404 or _twilio_code(resp) == 20404:
        return False  # expired, already used, or no verification in flight
    log.warning("Twilio Verify check for %s returned HTTP %s", _redact(phone), resp.status_code)
    raise SmsError("Couldn't check the code. Try again.")
