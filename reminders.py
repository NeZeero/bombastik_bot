import logging

from aiogram import Bot
from database import get_upcoming_unreminded, mark_reminded
from time_utils import parse_local_datetime

logger = logging.getLogger(__name__)

async def check_reminders(bot: Bot):
    rows = get_upcoming_unreminded()
    for row in rows:
        booking_id, date, time, name, phone, client_id = row
        if not client_id:
            mark_reminded(booking_id)
            continue

        try:
            dt = parse_local_datetime(date, time, "%Y-%m-%d %H:%M")
            date_str = dt.strftime("%d.%m.%Y")
            await bot.send_message(
                client_id,
                f"⏰ <b>Напоминание о записи</b>\n\n"
                f"Привет, {name}! Напоминаем, что у вас запись <b>{date_str}</b> в <b>{time}</b>.\n"
                f"Ждём вас! 💅",
                parse_mode="HTML"
            )
            mark_reminded(booking_id)
        except Exception:
            logger.exception("Failed to send reminder for booking_id=%s to client_id=%s", booking_id, client_id)
