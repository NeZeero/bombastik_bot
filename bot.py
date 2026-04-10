import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN
from handlers import client_v2, master
from database import init_db
from reminders import check_reminders

logging.basicConfig(level=logging.INFO)

async def main():
    init_db()

    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(master.router)
    dp.include_router(client_v2.router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_reminders, "interval", minutes=10, args=[bot])
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
