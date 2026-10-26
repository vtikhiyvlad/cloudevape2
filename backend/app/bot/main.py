"""
CloudVape Telegram Bot
Только авторизация продавцов. Отчёты админу уходят через API (notify).
Продажи — только в веб-кассе.
"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.core.config import get_settings
from app.bot.handlers import start

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


async def main():
    if not settings.BOT_TOKEN or settings.BOT_TOKEN.startswith("123456"):
        logger.error("❌ Укажи настоящий BOT_TOKEN в файле .env")
        return

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)

    logger.info("🤖 CloudVape бот (авторизация) запускается...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
