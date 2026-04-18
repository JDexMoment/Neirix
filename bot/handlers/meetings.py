import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from django.utils import timezone
from asgiref.sync import sync_to_async
from core.models import Meeting
from bot.utils import get_chat_context

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("meetings"))
async def cmd_meetings(message: Message):
    logger.info("meetings called")
    chat, topic, db_user = await get_chat_context(message)
    if not chat:
        await message.answer("Чат не определён")
        return

    # Получаем вообще все встречи в этом чате (без фильтра даты)
    get_all = sync_to_async(
        lambda: list(Meeting.objects.filter(topic__chat=chat).order_by('-start_at')[:10])
    )
    meetings = await get_all()
    logger.info(f"Total meetings in chat: {len(meetings)}")
    if not meetings:
        await message.answer("В этом чате нет ни одной встречи.")
        return

    lines = ["📅 <b>Все встречи в чате:</b>\n"]
    for m in meetings:
        lines.append(f"• {m.title} — {m.start_at.strftime('%d.%m.%Y %H:%M')}")
    await message.answer("\n".join(lines), parse_mode="HTML")