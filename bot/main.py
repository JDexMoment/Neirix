import os
import asyncio
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import django
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from aiogram.filters import Command
from aiogram.types import Message
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from bot.handlers import summary, tasks, meetings, chat_link, chat_events, messages, roles
from bot.handlers.settings import router as settings_router
from bot.middlewares.fsm_timeout import FSMTimeoutMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def set_bot_commands(bot: Bot):
    """Регистрирует команды бота в меню Telegram."""
    commands = [
        BotCommand(command="tasks", description="📋 Список открытых задач"),
        BotCommand(command="meetings", description="📅 Предстоящие встречи"),
        BotCommand(command="summary", description="📊 Саммари обсуждений"),
        BotCommand(command="link_chat", description="🔗 Получить код привязки чата"),
        BotCommand(command="role", description="👤 Управление ролями"),
        BotCommand(command="settings", description="⚙️ Настройки уведомлений"),
        BotCommand(command="help", description="📖 Справка по командам"),
    ]
    await bot.set_my_commands(
        commands=commands,
        scope=BotCommandScopeAllPrivateChats(),
    )
    await bot.set_my_commands(
        commands=commands,
        scope=BotCommandScopeAllGroupChats(),
    )
    await bot.set_my_commands(commands=commands)


async def main():
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(FSMTimeoutMiddleware())

    dp.include_router(chat_events.router)
    dp.include_router(chat_link.router)
    dp.include_router(summary.router)
    dp.include_router(tasks.router)
    dp.include_router(meetings.router)
    dp.include_router(messages.router)
    dp.include_router(roles.router)
    dp.include_router(settings_router)


    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        if message.chat.type == "private":
            await message.answer(
                "👋 Привет! Я Neirix — ваш рабочий ассистент.\n\n"
                "Чтобы я мог работать с контекстом вашей рабочей группы, выполните следующие шаги:\n"
                "1. Добавьте меня в группу и выдайте права администратора.\n"
                "2. В группе отправьте команду /link_chat — я пришлю код.\n"
                "3. Скопируйте код и отправьте его сюда, в личные сообщения.\n\n"
                "После привязки вам станут доступны команды /summary, /tasks, /meetings, /role, /settings."
            )
        else:
            await message.answer(
                "👋 Привет! Я готов помогать команде. Чтобы участники могли привязать чат к личным сообщениям, "
                "используйте команду /link_chat (доступна администраторам)."
            )


    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await message.answer(
            "📖 <b>Как пользоваться ботом</b>\n\n"
            "<b>📌 Работа в ЛС</b>\n"
            "Вы можете создавать задачи и встречи прямо из личных сообщений:\n"
            "• <code>задача для @user — сделать X до пятницы</code>\n"
            "• <code>назначь встречу с клиентом на завтра в 14:00 для @user</code>\n"
            "• <code>какие у меня задачи?</code> — список ваших задач\n"
            "• <code>кто участник встречи X?</code> — вопрос о конкретной\n"
            "• <code>перенеси встречу X на завтра</code> — изменение\n"
            "• <code>каждую пятницу @user должен отправлять отчет</code> — повторяющиеся\n\n"
            "Бот понимает естественный язык — пишите как человеку.\n\n"
            "<b>📋 Команды</b>\n"
            "/tasks [today|tomorrow|week|overdue] — задачи\n"
            "/meetings [today|tomorrow|week] — встречи\n"
            "/summary [today|yesterday|week|period] — саммари\n"
            "/settings — настройки уведомлений (только в ЛС)\n\n"
            "<b>👤 Роли</b>\n"
            "/role — показать свою роль\n"
            "/role list — список участников с ролями\n"
            "/role set @user manager — назначить менеджера (admin)\n"
            "/role set @user member — понизить (admin)\n\n"
            "• <b>admin</b> — полный доступ, управление ролями\n"
            "• <b>manager</b> — может создавать задачи и встречи\n"
            "• <b>member</b> — только чтение\n\n"
            "<b>🔧 Управление</b>\n"
            "Используйте кнопки под задачами и встречами:\n"
            "✅ Выполнено | ✏️ Редактировать | ❌ Отменить\n"
            "Для повторяющихся: 🛑 Отменить серию\n\n"
            "<b>🔗 Привязка чата</b>\n"
            "/link_chat — получить код привязки (в группе, admin)\n"
            "Отправьте код в ЛС боту.",
            parse_mode="HTML",
        )


    logger.info("Бот запущен")
    try:
        await set_bot_commands(bot)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
