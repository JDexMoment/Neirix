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
from bot.handlers import summary, tasks, meetings, chat_link, chat_events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Подключаем роутеры
    dp.include_router(chat_events.router)
    dp.include_router(chat_link.router)
    dp.include_router(summary.router)
    dp.include_router(tasks.router)
    dp.include_router(meetings.router)

    # Можно добавить простые команды /start и /help прямо здесь
    from aiogram.filters import Command
    from aiogram.types import Message

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        if message.chat.type == "private":
            await message.answer(
                "👋 Привет! Я Neirix — ваш рабочий ассистент.\n\n"
                "Чтобы я мог работать с контекстом вашей рабочей группы, выполните следующие шаги:\n"
                "1. Добавьте меня в группу и выдайте права администратора.\n"
                "2. В группе отправьте команду /link_chat — я пришлю код.\n"
                "3. Скопируйте код и отправьте его сюда, в личные сообщения.\n\n"
                "После привязки вам станут доступны команды /summary, /task, /meetings."
            )
        else:
            await message.answer(
                "👋 Привет! Я готов помогать команде. Чтобы участники могли привязать чат к личным сообщениям, "
                "используйте команду /link_chat (доступна администраторам)."
            )

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