from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE)

# ==========================================
# НАСТРОЙКИ БОТА
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        f"BOT_TOKEN is not set. Create {ENV_FILE} and add BOT_TOKEN=your_telegram_bot_token"
    )

MASTER_IDS = (
    884595697,
    8303372086,
)

BOOKINGS_PER_USER_PER_MONTH = 5
MASTER_CONTACT = "@321421"
SLOT_STEP_MINUTES = 15
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Europe/Moscow")
REMINDER_LEAD_HOURS = 24
REMINDER_WINDOW_MINUTES = 10

SERVICE_CATEGORIES = [
    {
        "id": "manicure",
        "title": "Маникюр",
        "services": [
            {"id": "classic_manicure", "title": "Классический маникюр", "duration_min": 60},
            {"id": "gel_polish", "title": "Маникюр с покрытием гель-лак", "duration_min": 90},
            {"id": "extension", "title": "Наращивание ногтей", "duration_min": 120},
            {"id": "nail_repair", "title": "Восстановление ногтей", "duration_min": 75},
            {"id": "gel_removal", "title": "Снятие покрытия", "duration_min": 30},
        ],
    },
    {
        "id": "pedicure",
        "title": "Педикюр",
        "services": [
            {"id": "classic_pedicure", "title": "Классический педикюр", "duration_min": 75},
            {"id": "pedicure_gel", "title": "Педикюр с покрытием", "duration_min": 105},
            {"id": "express_pedicure", "title": "Экспресс-педикюр", "duration_min": 45},
            {"id": "foot_care", "title": "Уход за стопами", "duration_min": 60},
            {"id": "pedicure_removal", "title": "Снятие покрытия на ногах", "duration_min": 30},
        ],
    },
]


def is_master(user_id: int) -> bool:
    return int(user_id) in MASTER_IDS


def get_service_categories() -> list[dict]:
    return SERVICE_CATEGORIES


def get_service_category(category_id: str) -> dict | None:
    for category in SERVICE_CATEGORIES:
        if category["id"] == category_id:
            return category
    return None


def get_all_services() -> dict[str, dict]:
    services: dict[str, dict] = {}
    for category in SERVICE_CATEGORIES:
        for service in category["services"]:
            services[service["id"]] = {
                **service,
                "category_id": category["id"],
                "category_title": category["title"],
            }
    return services


def get_service(service_id: str) -> dict | None:
    return get_all_services().get(service_id)


def round_duration_to_step(minutes: int) -> int:
    if minutes <= 0:
        return SLOT_STEP_MINUTES
    remainder = minutes % SLOT_STEP_MINUTES
    return minutes if remainder == 0 else minutes + (SLOT_STEP_MINUTES - remainder)


def calculate_booking_duration(service_ids: list[str]) -> int:
    durations = sorted(
        [get_service(service_id)["duration_min"] for service_id in service_ids if get_service(service_id)],
        reverse=True,
    )
    if not durations:
        return SLOT_STEP_MINUTES
    if len(durations) == 1:
        return round_duration_to_step(durations[0])

    longest = durations[0]
    extra = sum(duration / 2 for duration in durations[1:])
    return round_duration_to_step(int(longest + extra))


def format_service_names(service_ids: list[str]) -> str:
    names = [get_service(service_id)["title"] for service_id in service_ids if get_service(service_id)]
    return ", ".join(names)


PORTFOLIO_TEXT = (
    "✨ <b>Моё портфолио</b>\n\n"
    "Здесь вы можете посмотреть мои работы.\n"
    "Instagram: @ваш_ник\n"
    "VK: vk.com/ваша_страница"
)

ADDRESS_TEXT = (
    "📍 <b>Наш адрес</b>\n\n"
    "г. Москва, ул. Примерная, д. 1\n"
    "Метро: Примерная (5 мин пешком)\n\n"
    "Режим работы:\n"
    "Пн–Сб: 10:00 – 20:00\n"
    "Вс: по записи"
)
