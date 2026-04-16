from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import Message
from core.services.meeting_service import MeetingService

router = Router()
meeting_service = MeetingService()


@router.message(Command("meetings"))
async def cmd_meetings(message: Message, chat, topic):
    """Показывает предстоящие встречи в этом чате/теме"""
    if not chat:
        await message.answer("Эту команду нужно выполнять в группе.")
        return

    from core.models import Meeting
    from django.utils import timezone

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