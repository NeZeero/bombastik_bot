import sqlite3
from datetime import datetime
from pathlib import Path

# Всегда один и тот же файл БД, даже если запускать бота из другой папки
DB_PATH = str(Path(__file__).resolve().parent / "beauty_bot.db")


def _time_sort_key(t: str):
    """Сортировка времени слота (ЧЧ:ММ), не лексикографически (9:00 раньше 10:00)."""
    try:
        parts = (t or "").strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return (h, m)
    except (ValueError, IndexError):
        return (99, 99)


def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    with get_conn() as conn:
        conn.executescript("""
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
                client_name TEXT NOT NULL,
                client_phone TEXT NOT NULL,
                client_id INTEGER,
                client_username TEXT,
                reminded INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bookings)").fetchall()}
        if "client_username" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN client_username TEXT")
            conn.commit()

# ---------- Слоты (расписание мастера) ----------

def add_slot(date: str, time: str):
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO work_slots (slot_date, slot_time) VALUES (?, ?)",
                (date, time)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass

def delete_slot(date: str, time: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM bookings WHERE slot_date=? AND slot_time=?",
            (date, time),
        )
        conn.execute(
            "DELETE FROM work_slots WHERE slot_date=? AND slot_time=?",
            (date, time),
        )
        conn.commit()

def get_work_dates():
    """Возвращает все рабочие даты (уникальные), не раньше сегодня."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT slot_date FROM work_slots WHERE slot_date >= ? ORDER BY slot_date",
            (today,)
        ).fetchall()
    return [r[0] for r in rows]

def get_slots_for_date(date: str):
    """Возвращает все слоты на дату с пометкой занятости."""
    with get_conn() as conn:
        slots = conn.execute(
            "SELECT slot_time FROM work_slots WHERE slot_date=? ORDER BY slot_time",
            (date,)
        ).fetchall()
        booked = conn.execute(
            "SELECT slot_time FROM bookings WHERE slot_date=?",
            (date,)
        ).fetchall()
    booked_times = {r[0] for r in booked}
    result = [(r[0], r[0] in booked_times) for r in slots]
    result.sort(key=lambda x: _time_sort_key(x[0]))

    # Фильтровать прошедшие слоты для сегодняшней даты
    today = datetime.now().strftime("%Y-%m-%d")
    if date == today:
        now = datetime.now()
        current_time = now.time()
        filtered_result = []
        for slot in result:
            try:
                slot_time = datetime.strptime(slot[0], "%H:%M").time()
                if slot_time > current_time:
                    filtered_result.append(slot)
            except ValueError:
                # Если формат времени неправильный, пропустить
                continue
        result = filtered_result

    return result

def is_slot_free(date: str, time: str) -> bool:
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM work_slots WHERE slot_date=? AND slot_time=?",
            (date, time)
        ).fetchone()
        if not exists:
            return False
        booked = conn.execute(
            "SELECT 1 FROM bookings WHERE slot_date=? AND slot_time=?",
            (date, time)
        ).fetchone()
    return booked is None

# ---------- Записи ----------

def create_booking(
    date: str,
    time: str,
    name: str,
    phone: str,
    client_id: int,
    client_username: str | None = None,
):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bookings (slot_date, slot_time, client_name, client_phone, client_id, client_username) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, time, name, phone, client_id, client_username),
        )
        conn.commit()


def count_bookings_month_for_client(client_id: int, year_month: str) -> int:
    """Сколько записей у клиента на даты в календарном месяце (year_month = 'YYYY-MM')."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE client_id=? AND substr(slot_date, 1, 7)=?",
            (client_id, year_month),
        ).fetchone()
    return int(row[0]) if row else 0


def get_last_client_name(client_id: int) -> str | None:
    """Последнее имя, которое клиент использовал при записи."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT client_name FROM bookings WHERE client_id=? ORDER BY id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
    return row[0] if row and row[0] else None


def get_booked_dates():
    """Даты, на которые есть хотя бы одна запись (не раньше сегодня)."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT slot_date FROM bookings WHERE slot_date >= ? ORDER BY slot_date",
            (today,)
        ).fetchall()
    return [r[0] for r in rows]

def get_bookings_for_date(date: str):
    """Все записи на дату (для мастера)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, slot_time, client_name, client_phone, client_id "
            "FROM bookings WHERE slot_date=?",
            (date,),
        ).fetchall()
    rows.sort(key=lambda r: _time_sort_key(r[1]))
    return rows

def cancel_booking(booking_id: int):
    """Удаляет запись и возвращает данные записи (если была)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT slot_date, slot_time, client_name, client_phone, client_id "
            "FROM bookings WHERE id=?",
            (booking_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM bookings WHERE id=?", (booking_id,))
        conn.commit()
    return row

def get_upcoming_unreminded():
    """Записи в ближайшие 4–4.5 часа без напоминания."""
    now = datetime.now()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, slot_date, slot_time, client_name, client_phone, client_id "
            "FROM bookings WHERE reminded=0 ORDER BY slot_date, slot_time"
        ).fetchall()
    result = []
    for row in rows:
        try:
            dt = datetime.strptime(f"{row[1]} {row[2]}", "%Y-%m-%d %H:%M")
            diff = (dt - now).total_seconds() / 3600
            if 3.5 <= diff <= 4.5:
                result.append(row)
        except ValueError:
            pass
    return result

def mark_reminded(booking_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE bookings SET reminded=1 WHERE id=?", (booking_id,))

def get_all_bookings():
    """Только предстоящие записи: есть слот в расписании и время визита ещё не прошло."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT b.slot_date, b.slot_time, b.client_name, b.client_phone, b.client_id, b.client_username
            FROM bookings b
            INNER JOIN work_slots w
                ON w.slot_date = b.slot_date AND w.slot_time = b.slot_time
            WHERE b.slot_date >= ?
            """,
            (today,),
        ).fetchall()
    active = []
    for r in rows:
        try:
            dt = datetime.strptime(f"{r[0]} {r[1]}", "%Y-%m-%d %H:%M")
            if dt >= now:
                active.append(r)
        except ValueError:
            continue
    active.sort(key=lambda r: (r[0], _time_sort_key(r[1])))
    return active
