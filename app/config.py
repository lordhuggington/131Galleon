"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    db_path: str
    public_url: str
    cookie_secure: bool
    session_days: int
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_verify_service_sid: str
    photos_dir: str

    @property
    def sms_enabled(self) -> bool:
        """Text-message sign-in works only when all three Twilio values are set."""
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_verify_service_sid)


def get_config() -> Config:
    return Config(
        db_path=os.environ.get("HRS_DB_PATH", "data/house.db"),
        # The address Claude reaches this app on. Every OAuth metadata URL and the connector URL are
        # built from it, so a trailing slash would produce '…//mcp'.
        public_url=os.environ.get("HRS_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        cookie_secure=_bool("HRS_COOKIE_SECURE", True),
        session_days=int(os.environ.get("HRS_SESSION_DAYS", "30")),
        twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID", "").strip(),
        twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
        twilio_verify_service_sid=os.environ.get("TWILIO_VERIFY_SERVICE_SID", "").strip(),
        photos_dir=os.environ.get("HRS_PHOTOS_DIR", "data/photos"),
    )
