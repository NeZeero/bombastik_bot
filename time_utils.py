from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import BOT_TIMEZONE

LOCAL_TIMEZONE = ZoneInfo(BOT_TIMEZONE)


def now_local() -> datetime:
    return datetime.now(LOCAL_TIMEZONE)


def today_local() -> date:
    return now_local().date()


def parse_local_datetime(date_value: str, time_value: str, fmt: str) -> datetime:
    return datetime.strptime(f"{date_value} {time_value}", fmt).replace(tzinfo=LOCAL_TIMEZONE)
