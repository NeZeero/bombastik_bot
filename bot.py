import asyncio
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

from config import BOT_TOKEN
from handlers import client_v2, master
from database import init_db, repair_db
from reminders import check_reminders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

async def _delayed_startup(bot: Bot, scheduler: AsyncIOScheduler):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as exc:
        logger.warning("Webhook skip (non-critical): %s", exc)

    try:
        await asyncio.to_thread(init_db)
    except Exception as exc:
        logger.exception("init_db failed: %s", exc)

    try:
        bot_id = int(BOT_TOKEN.split(":", 1)[0]) if ":" in BOT_TOKEN else None
        repair_stats = await asyncio.to_thread(repair_db, bot_id=bot_id) if bot_id is not None else {}
        logger.info("Database repair stats: %s", repair_stats)
    except Exception as exc:
        logger.exception("repair_db failed: %s", exc)

    scheduler.add_job(check_reminders, "interval", minutes=10, args=[bot])
    scheduler.start()

async def startup_handler(dispatcher: Dispatcher, bot: Bot):
    asyncio.create_task(_delayed_startup(bot, scheduler))

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Define it in .env or environment variables.")

    bot = Bot(token=BOT_TOKEN, request_timeout=120)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(master.router)
    dp.include_router(client_v2.router)

    dp.startup.register(startup_handler)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
