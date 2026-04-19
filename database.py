from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from config import REMINDER_LEAD_HOURS, REMINDER_WINDOW_MINUTES, SLOT_STEP_MINUTES
from time_utils import now_local, parse_local_datetime

DB_PATH = Path(__file__).resolve().parent / "beauty_bot.db"
TIME_FORMAT = "%H:%M"
DATETIME_FORMAT = "%Y-%m-%d %H:%M"


def get_conn():
    return sqlite3.connect(DB_PATH)


def normalize_slot_time(time_value: str) -> str:
    return datetime.strptime((time_value or "").strip(), TIME_FORMAT).strftime(TIME_FORMAT)


def parse_slot_datetime(date: str, time_value: str) -> datetime:
    return parse_local_datetime(date, normalize_slot_time(time_value), DATETIME_FORMAT)


def _format_time(dt: datetime) -> str:
    return dt.strftime(TIME_FORMAT)


def _time_sort_key(t: str) -> tuple[int, int]:
    normalized = normalize_slot_time(t)
    hours, minutes = normalized.split(":")
    return int(hours), int(minutes)


def _step_delta() -> timedelta:
    return timedelta(minutes=SLOT_STEP_MINUTES)


def _round_up_to_step(minutes: int) -> int:
    remainder = minutes % SLOT_STEP_MINUTES
    return minutes if remainder == 0 else minutes + (SLOT_STEP_MINUTES - remainder)


def _generate_range_slots(start_time: str, end_time: str) -> list[str]:
    start_dt = datetime.strptime(normalize_slot_time(start_time), TIME_FORMAT)
    end_dt = datetime.strptime(normalize_slot_time(end_time), TIME_FORMAT)
    if start_dt >= end_dt:
        raise ValueError("Начало интервала должно быть раньше конца")
    start_minutes = start_dt.hour * 60 + start_dt.minute
    end_minutes = end_dt.hour * 60 + end_dt.minute
    if start_minutes % SLOT_STEP_MINUTES != 0 or end_minutes % SLOT_STEP_MINUTES != 0:
        raise ValueError(f"Время должно быть кратно {SLOT_STEP_MINUTES} минутам")

    current = start_dt
    slots: list[str] = []
    while current < end_dt:
        slots.append(_format_time(current))
        current += _step_delta()
    return slots


def parse_work_ranges(raw_text: str) -> list[tuple[str, str]]:
    tokens = [token for token in raw_text.replace(",", " ").split() if token]
    if not tokens:
        raise ValueError("Укажите хотя бы один интервал")

    ranges: list[tuple[str, str]] = []
    for token in tokens:
        if "-" not in token:
            raise ValueError(f"Неверный формат интервала: {token}")
        start_time, end_time = token.split("-", 1)
        start_normalized = normalize_slot_time(start_time)
        end_normalized = normalize_slot_time(end_time)
        _generate_range_slots(start_normalized, end_normalized)
        ranges.append((start_normalized, end_normalized))
    return ranges


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS work_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_date TEXT NOT NULL,
                slot_time TEXT NOT NULL,
                UNIQUE(slot_date, slot_time)
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_date TEXT NOT NULL,
                slot_time TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL DEFAULT 15,
                service_ids TEXT,
                service_names TEXT,
                client_name TEXT NOT NULL,
                client_phone TEXT NOT NULL,
                client_id INTEGER,
                client_username TEXT,
                reminded INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(bookings)").fetchall()}
        if "client_username" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN client_username TEXT")
        if "duration_minutes" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 15")
        if "service_ids" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN service_ids TEXT")
        if "service_names" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN service_names TEXT")



def repair_db(bot_id: int | None = None) -> dict[str, int]:
    stats = {
        "normalized_work_slots": 0,
        "normalized_bookings": 0,
        "sanitized_bot_bookings": 0,
    }

    with get_conn() as conn:
        for slot_id, slot_date, slot_time in conn.execute(
            "SELECT id, slot_date, slot_time FROM work_slots ORDER BY id"
        ).fetchall():
            normalized_time = normalize_slot_time(slot_time)
            if normalized_time == slot_time:
                continue
            existing = conn.execute(
                "SELECT id FROM work_slots WHERE slot_date=? AND slot_time=?",
                (slot_date, normalized_time),
            ).fetchone()
            if existing and existing[0] != slot_id:
                conn.execute("DELETE FROM work_slots WHERE id=?", (slot_id,))
            else:
                conn.execute(
                    "UPDATE work_slots SET slot_time=? WHERE id=?",
                    (normalized_time, slot_id),
                )
            stats["normalized_work_slots"] += 1

        for booking_id, slot_time in conn.execute(
            "SELECT id, slot_time FROM bookings ORDER BY id"
        ).fetchall():
            normalized_time = normalize_slot_time(slot_time)
            if normalized_time == slot_time:
                continue
            conn.execute(
                "UPDATE bookings SET slot_time=? WHERE id=?",
                (normalized_time, booking_id),
            )
            stats["normalized_bookings"] += 1

        if bot_id is not None:
            cursor = conn.execute(
                """
                UPDATE bookings
                SET client_id=NULL,
                    client_username=NULL,
                    reminded=1
                WHERE client_id=?
                """,
                (bot_id,),
            )
            stats["sanitized_bot_bookings"] = cursor.rowcount

    return stats


def add_slot(date: str, time: str) -> bool:
    normalized_time = normalize_slot_time(time)
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO work_slots (slot_date, slot_time) VALUES (?, ?)",
            (date, normalized_time),
        )
    return cursor.rowcount > 0


def add_work_ranges(date: str, ranges: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    added: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for start_time, end_time in ranges:
        for slot_time in _generate_range_slots(start_time, end_time):
            if slot_time in seen:
                continue
            seen.add(slot_time)
            if add_slot(date, slot_time):
                added.append(slot_time)
            else:
                skipped.append(slot_time)
    return added, skipped


def _get_day_work_slots(date: str) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slot_time FROM work_slots WHERE slot_date=? ORDER BY slot_time",
            (date,),
        ).fetchall()
    return [row[0] for row in rows]


def get_work_dates() -> list[str]:
    today = now_local().strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT slot_date FROM work_slots WHERE slot_date >= ? ORDER BY slot_date",
            (today,),
        ).fetchall()
    return [row[0] for row in rows]


def _get_bookings_for_overlap(date: str) -> list[tuple[str, int]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slot_time, COALESCE(duration_minutes, ?) FROM bookings WHERE slot_date=?",
            (SLOT_STEP_MINUTES, date),
        ).fetchall()
    return [(row[0], int(row[1] or SLOT_STEP_MINUTES)) for row in rows]


def _interval_overlaps(start_a: datetime, duration_a: int, start_b: datetime, duration_b: int) -> bool:
    end_a = start_a + timedelta(minutes=duration_a)
    end_b = start_b + timedelta(minutes=duration_b)
    return start_a < end_b and start_b < end_a


def _has_contiguous_work_slots(date: str, start_time: str, duration_minutes: int, work_slots_set: set[str]) -> bool:
    normalized_duration = _round_up_to_step(duration_minutes)
    current = parse_slot_datetime(date, start_time)
    end_dt = current + timedelta(minutes=normalized_duration)
    while current < end_dt:
        if _format_time(current) not in work_slots_set:
            return False
        current += _step_delta()
    return True


def is_slot_free(date: str, time: str, duration_minutes: int = SLOT_STEP_MINUTES) -> bool:
    normalized_time = normalize_slot_time(time)
    work_slots = _get_day_work_slots(date)
    work_slots_set = set(work_slots)
    if normalized_time not in work_slots_set:
        return False
    if not _has_contiguous_work_slots(date, normalized_time, duration_minutes, work_slots_set):
        return False

    start_dt = parse_slot_datetime(date, normalized_time)
    for booked_time, booked_duration in _get_bookings_for_overlap(date):
        booking_dt = parse_slot_datetime(date, booked_time)
        if _interval_overlaps(start_dt, duration_minutes, booking_dt, booked_duration):
            return False
    return True


def get_available_dates(duration_minutes: int = SLOT_STEP_MINUTES) -> list[str]:
    today = now_local().strftime("%Y-%m-%d")
    dates = get_work_dates()
    result: list[str] = []
    for date in dates:
        if date < today:
            continue
        slots = get_slots_for_date(date, duration_minutes)
        if any(not is_busy for _, is_busy in slots):
            result.append(date)
    return result


def _build_slots_for_date(
    date: str,
    duration_minutes: int | None = None,
    *,
    include_past: bool,
) -> list[tuple[str, bool]]:
    work_slots = _get_day_work_slots(date)
    if not work_slots:
        return []

    bookings = _get_bookings_for_overlap(date)
    result: list[tuple[str, bool]] = []
    work_slots_set = set(work_slots)
    now = now_local()

    for slot_time in work_slots:
        slot_dt = parse_slot_datetime(date, slot_time)
        if not include_past and slot_dt <= now:
            continue

        if duration_minutes is None:
            is_busy = False
            for booked_time, booked_duration in bookings:
                booking_dt = parse_slot_datetime(date, booked_time)
                if _interval_overlaps(slot_dt, SLOT_STEP_MINUTES, booking_dt, booked_duration):
                    is_busy = True
                    break
        else:
            is_busy = not is_slot_free(date, slot_time, duration_minutes)
        result.append((slot_time, is_busy))

    return result


def get_slots_for_date(date: str, duration_minutes: int | None = None) -> list[tuple[str, bool]]:
    return _build_slots_for_date(date, duration_minutes, include_past=False)


def get_slots_for_date_full(date: str, duration_minutes: int | None = None) -> list[tuple[str, bool]]:
    return _build_slots_for_date(date, duration_minutes, include_past=True)


def get_work_ranges_for_date(date: str) -> list[str]:
    work_slots = _get_day_work_slots(date)
    if not work_slots:
        return []
    times = [datetime.strptime(slot_time, TIME_FORMAT) for slot_time in work_slots]
    step = _step_delta()
    ranges: list[str] = []
    start = times[0]
    prev = times[0]
    for current in times[1:]:
        if current - prev != step:
            ranges.append(f"{_format_time(start)}-{_format_time(prev + step)}")
            start = current
        prev = current
    ranges.append(f"{_format_time(start)}-{_format_time(prev + step)}")
    return ranges


def _is_legacy_day_times(work_slots: list[str]) -> bool:
    if len(work_slots) <= 1:
        return True
    times = [datetime.strptime(slot_time, TIME_FORMAT) for slot_time in work_slots]
    for prev_dt, next_dt in zip(times, times[1:]):
        if next_dt - prev_dt == _step_delta():
            return False
    return True


def purge_legacy_work_days() -> dict[str, int]:
    stats = {"days_removed": 0, "work_slots_removed": 0, "bookings_removed": 0}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slot_date, slot_time FROM work_slots ORDER BY slot_date, slot_time"
        ).fetchall()
        if not rows:
            return stats

        date_map: dict[str, list[str]] = {}
        for slot_date, slot_time in rows:
            date_map.setdefault(slot_date, []).append(slot_time)

        legacy_dates = [date for date, times in date_map.items() if _is_legacy_day_times(times)]
        if not legacy_dates:
            return stats

        for date in legacy_dates:
            cursor = conn.execute("DELETE FROM work_slots WHERE slot_date=?", (date,))
            stats["work_slots_removed"] += cursor.rowcount
            cursor = conn.execute("DELETE FROM bookings WHERE slot_date=?", (date,))
            stats["bookings_removed"] += cursor.rowcount
        stats["days_removed"] = len(legacy_dates)
    return stats


def create_booking(
    date: str,
    time: str,
    name: str,
    phone: str,
    client_id: int,
    client_username: str | None = None,
    *,
    duration_minutes: int,
    service_ids: list[str],
    service_names: str,
):
    normalized_time = normalize_slot_time(time)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bookings (
                slot_date, slot_time, duration_minutes, service_ids, service_names,
                client_name, client_phone, client_id, client_username
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date,
                normalized_time,
                duration_minutes,
                ",".join(service_ids),
                service_names,
                name,
                phone,
                client_id,
                client_username,
            ),
        )


def count_bookings_month_for_client(client_id: int, year_month: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE client_id=? AND substr(slot_date, 1, 7)=?",
            (client_id, year_month),
        ).fetchone()
    return int(row[0]) if row else 0


def get_last_client_name(client_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT client_name FROM bookings WHERE client_id=? ORDER BY id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
    return row[0] if row and row[0] else None


def get_booked_dates() -> list[str]:
    now = now_local()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT slot_date FROM bookings ORDER BY slot_date"
        ).fetchall()
    result: list[str] = []
    for (date,) in rows:
        with get_conn() as conn:
            day_rows = conn.execute(
                "SELECT slot_time, COALESCE(duration_minutes, ?) FROM bookings WHERE slot_date=? ORDER BY slot_time",
                (SLOT_STEP_MINUTES, date),
            ).fetchall()
        if any(parse_slot_datetime(date, row[0]) + timedelta(minutes=int(row[1] or SLOT_STEP_MINUTES)) > now for row in day_rows):
            result.append(date)
    return result


def get_bookings_for_date(date: str):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, slot_time, client_name, client_phone, client_id,
                   COALESCE(service_names, ''), COALESCE(duration_minutes, ?)
            FROM bookings
            WHERE slot_date=?
            ORDER BY slot_time
            """,
            (SLOT_STEP_MINUTES, date),
        ).fetchall()
    return rows


def cancel_booking(booking_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT slot_date, slot_time, client_name, client_phone, client_id FROM bookings WHERE id=?",
            (booking_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM bookings WHERE id=?", (booking_id,))
    return row


def delete_slot(date: str, time: str):
    normalized_time = normalize_slot_time(time)
    with get_conn() as conn:
        conn.execute("DELETE FROM bookings WHERE slot_date=? AND slot_time=?", (date, normalized_time))
        conn.execute("DELETE FROM work_slots WHERE slot_date=? AND slot_time=?", (date, normalized_time))


def get_upcoming_unreminded():
    now = now_local()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, slot_date, slot_time, client_name, client_phone, client_id FROM bookings WHERE reminded=0 ORDER BY slot_date, slot_time"
        ).fetchall()
    result = []
    lead_seconds = REMINDER_LEAD_HOURS * 3600
    window_seconds = REMINDER_WINDOW_MINUTES * 60
    for row in rows:
        try:
            dt = parse_slot_datetime(row[1], row[2])
            diff_seconds = (dt - now).total_seconds()
            if lead_seconds - window_seconds <= diff_seconds <= lead_seconds:
                result.append(row)
        except ValueError:
            continue
    return result


def mark_reminded(booking_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE bookings SET reminded=1 WHERE id=?", (booking_id,))


def get_all_bookings():
    now = now_local()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT slot_date, slot_time, client_name, client_phone, client_id, client_username,
                   COALESCE(service_names, ''), COALESCE(duration_minutes, ?)
            FROM bookings
            ORDER BY slot_date ASC, slot_time ASC
            """,
            (SLOT_STEP_MINUTES,),
        ).fetchall()
    active = []
    for row in rows:
        try:
            dt = parse_slot_datetime(row[0], row[1])
            duration = int(row[7] or SLOT_STEP_MINUTES)
            if dt + timedelta(minutes=duration) > now:
                active.append(row)
        except ValueError:
            continue
    return active
