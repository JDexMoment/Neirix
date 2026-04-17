from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from django.utils import timezone
from asgiref.sync import sync_to_async
from core.models import Meeting
from bot.utils import get_chat_context

router = Router()


@router.message(Command("meetings"))
async def cmd_meetings(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not chat:
        return

    filters = {'topic__chat': chat}
    if topic:
        filters['topic'] = topic

    # Оборачиваем синхронный запрос в sync_to_async
    get_upcoming = sync_to_async(
        lambda: list(
            Meeting.objects.filter(
                **filters,
                start_at__gte=timezone.now()
            ).order_by('start_at')[:10]
        )
    )
    upcoming = await get_upcoming()

    if not upcoming:
        await message.answer("Нет запланированных встреч.")
        return

    lines = ["📅 <b>Предстоящие встречи:</b>\n"]
    for m in upcoming:
        # Получаем участников (many-to-many) тоже через sync_to_async
        get_participants = sync_to_async(lambda: list(m.participants.all()))
        participants = await get_participants()
        participants_str = ", ".join([p.full_name for p in participants])
        lines.append(f"• <b>{m.title}</b> — {m.start_at.strftime('%d.%m.%Y %H:%M')}")
        if participants_str:
            lines.append(f"  Участники: {participants_str}")

    await message.answer("\n".join(lines), parse_mode="HTML")