"""Passwords (scrypt), cookie sessions and login throttling."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import get_config
from .db import now_iso

COOKIE_NAME = "hrs_session"
ROLES = ("owner", "staff")
MIN_PASSWORD_LEN = 10

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=64 * 1024 * 1024, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=len(expected))
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    return None


def normalize_phone(raw: str) -> str:
    """Turn messy user input into E.164 ('+13105551234'). Store and compare only this form."""
    from .api import ApiError  # imported here: app.api imports this module at start-up

    text = (raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        if 8 <= len(digits) <= 15:
            return "+" + digits
    elif len(digits) == 10:
        return "+1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise ApiError(400, "Enter a mobile number like (310) 555-1234.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    days = get_config().session_days
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_token_hash(token), user_id, now_iso(), expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
    )
    # Housekeeping: drop expired sessions.
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    return token, days * 86400


def user_for_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute(
        """SELECT u.id, u.username, u.display_name, u.role, u.label, u.phone, u.door_code, u.can_see_meals
           FROM sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1""",
        (_token_hash(token), now_iso()),
    ).fetchone()


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def delete_user_sessions(conn: sqlite3.Connection, user_id: int, keep_token: str | None = None) -> None:
    if keep_token:
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, _token_hash(keep_token)))
    else:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


class LoginThrottle:
    """In-memory lockout: 5 failed attempts per username within 15 minutes locks that username for 15 minutes."""

    def __init__(self, limit: int = 5, window: int = 900):
        self.limit, self.window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            recent = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = recent
            return len(recent) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


throttle = LoginThrottle()
# Rate limit on outgoing texts, keyed by phone number: 3 sends per 10 minutes. Unlike `throttle`
# this is never reset by a success — it is a rate limit, not a lockout.
send_throttle = LoginThrottle(limit=3, window=600)
