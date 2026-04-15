import asyncio
import logging
import signal

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN
from handlers import client_v2, master
from database import init_db, repair_db
from reminders import check_reminders

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")
signal.signal(signal.SIGINT, signal.SIG_IGN)

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
    if not scheduler.running:
        scheduler.start()

async def safe_start(bot: Bot, scheduler: AsyncIOScheduler):
    try:
        await _delayed_startup(bot, scheduler)
    except Exception as exc:
        logger.exception("startup crash prevented: %s", exc)

async def startup_handler(dispatcher: Dispatcher, bot: Bot):
    asyncio.create_task(safe_start(bot, scheduler))

async def main():
    connector = aiohttp.TCPConnector(force_close=True)
    bot = Bot(
        token=BOT_TOKEN,
        request_timeout=120,
        connector=connector,
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(master.router)
    dp.include_router(client_v2.router)

    dp.startup.register(startup_handler)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
