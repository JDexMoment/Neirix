import os
import asyncio
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

print("BASE_DIR:", BASE_DIR)
print("sys.path includes BASE_DIR:", str(BASE_DIR) in sys.path)
print("Files in BASE_DIR:", list(BASE_DIR.iterdir()))
print("Files in config:", list((BASE_DIR / 'config').iterdir()))

import django
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from django.conf import settings

# Настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()


# Импортируем роутеры из handlers
from bot.handlers import summary, tasks, meetings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Подключаем роутеры
    dp.include_router(summary.router)
    dp.include_router(tasks.router)
    dp.include_router(meetings.router)

    # Можно добавить простые команды /start и /help прямо здесь
    from aiogram.filters import Command
    from aiogram.types import Message

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        await message.answer("Привет! Я Neirix — ваш рабочий ассистент. Используйте /help для списка команд.")

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await message.answer(
            "/summary [today|yesterday|week|YYYY-MM-DD YYYY-MM-DD] — саммари переписки\n"
            "/task list — список ваших задач\n"
            "/task done &lt;номер&gt; — отметить задачу выполненной\n"
            "/meetings — предстоящие встречи"
        )

    logger.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())