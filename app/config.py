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
    anthropic_api_key: str
    anthropic_model: str
    cookie_secure: bool
    session_days: int


def get_config() -> Config:
    return Config(
        db_path=os.environ.get("HRS_DB_PATH", "data/house.db"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        cookie_secure=_bool("HRS_COOKIE_SECURE", True),
        session_days=int(os.environ.get("HRS_SESSION_DAYS", "30")),
    )
