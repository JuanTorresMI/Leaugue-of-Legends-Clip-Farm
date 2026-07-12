"""Local per-platform daily upload quota tracking (SQLite-backed, resets by calendar date).

Slots are counted only on *successful* uploads: callers `check()` before attempting and
`record_success()` after the platform confirms, so a failed upload doesn't burn a slot.
"""
from __future__ import annotations

from datetime import date

from clipfarm import db


def check(platform: str, daily_limit: int) -> bool:
    """True if another upload is allowed today."""
    today = date.today().isoformat()
    with db.get_conn() as conn:
        return db.get_quota(conn, platform, today) < daily_limit


def record_success(platform: str) -> None:
    today = date.today().isoformat()
    with db.get_conn() as conn:
        db.increment_quota(conn, platform, today)


def get_usage(platform: str) -> int:
    today = date.today().isoformat()
    with db.get_conn() as conn:
        return db.get_quota(conn, platform, today)
