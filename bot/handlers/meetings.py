from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from django.utils import timezone
from core.models import Meeting
from bot.utils import get_chat_context

router = Router()


@router.message(Command("meetings"))
async def cmd_meetings(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not chat:
        return  # пользователь уже получил сообщение с инструкцией

    # Формируем фильтр для встреч
    filters = {'topic__chat': chat}
    if topic:
        filters['topic'] = topic

    upcoming = Meeting.objects.filter(
        **filters,
        start_at__gte=timezone.now()
    ).order_by('start_at')[:10]

    if not upcoming:
        await message.answer("Нет запланированных встреч.")
        return

    lines = ["📅 <b>Предстоящие встречи:</b>\n"]
    for m in upcoming:
        participants = ", ".join([p.full_name for p in m.participants.all()])
        lines.append(f"• <b>{m.title}</b> — {m.start_at.strftime('%d.%m.%Y %H:%M')}")
        if participants:
            lines.append(f"  Участники: {participants}")
    await message.answer("\n".join(lines), parse_mode="HTML")