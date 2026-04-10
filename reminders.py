from aiogram import Bot
from database import get_upcoming_unreminded, mark_reminded

async def check_reminders(bot: Bot):
    rows = get_upcoming_unreminded()
    for row in rows:
        booking_id, date, time, name, phone, client_id = row
        if client_id:
            try:
                # Переформатируем дату для читаемости
                from datetime import datetime
                dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
                date_str = dt.strftime("%d.%m.%Y")
                await bot.send_message(
                    client_id,
                    f"⏰ <b>Напоминание о записи</b>\n\n"
                    f"Привет, {name}! Напоминаем, что у вас запись сегодня в <b>{time}</b>.\n"
                    f"Ждём вас! 💅",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        mark_reminded(booking_id)
