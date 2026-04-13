import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, TOKEN
from handlers import client_v2, master
from database import init_db, repair_db
from reminders import check_reminders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def delete_webhook_with_retry(
    bot: Bot,
    attempts: int = 5,
    base_delay: int = 5,
):
    for attempt in range(1, attempts + 1):
        try:
            await bot.delete_webhook(drop_pending_updates=True, request_timeout=30)
            return
        except TelegramNetworkError as exc:
            if attempt == attempts:
                raise
            delay = base_delay * attempt
            logger.warning(
                "Telegram API timeout on startup (attempt %s/%s). Retrying in %s seconds.",
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)

async def main():
    init_db()
    token = TOKEN or BOT_TOKEN
    if not token:
        raise RuntimeError("BOT_TOKEN/TOKEN is not set. Define it in .env or environment variables.")
    bot_id = int(token.split(":", 1)[0])
    repair_stats = repair_db(bot_id=bot_id)
    logger.info("Database repair stats: %s", repair_stats)

    bot = Bot(token=token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(master.router)
    dp.include_router(client_v2.router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_reminders, "interval", minutes=10, args=[bot])
    scheduler.start()

    try:
        await delete_webhook_with_retry(bot)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
