import asyncio
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

from config import BOT_TOKEN
from handlers import client_v2, master
from database import init_db, repair_db
from reminders import check_reminders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    init_db()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Define it in .env or environment variables.")
    bot_id = int(BOT_TOKEN.split(":", 1)[0])
    repair_stats = repair_db(bot_id=bot_id)
    logger.info("Database repair stats: %s", repair_stats)

    bot = Bot(token=BOT_TOKEN, request_timeout=60)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(master.router)
    dp.include_router(client_v2.router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_reminders, "interval", minutes=10, args=[bot])
    scheduler.start()

    try:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as exc:
            logger.warning("Webhook skip (non-critical): %s", exc)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
