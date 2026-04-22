import logging
from typing import List, Optional

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from asgiref.sync import sync_to_async
from django.db.models import Q
from django.utils import timezone

from core.models import Meeting
from bot.utils import get_chat_context

logger = logging.getLogger(__name__)
router = Router()


def _get_upcoming_meetings_for_private(db_user, chat=None) -> List[Meeting]:
    """
    Для лички:
    - встречи, где пользователь указан участником
    - плюс встречи привязанного чата, если chat есть
    """
    now = timezone.now()

    query = Q(participants=db_user)
    if chat is not None:
        query |= Q(topic__chat=chat)

    return list(
        Meeting.objects.filter(
            query,
            start_at__gte=now,
        )
        .select_related("topic", "topic__chat")
        .prefetch_related("participants")
        .order_by("start_at", "id")
        .distinct()
    )


def _get_upcoming_meetings_for_chat(chat, topic=None) -> List[Meeting]:
    """
    Для группы:
    - встречи этого чата
    - если команда вызвана внутри topic/thread, то только встречи этого topic
    """
    now = timezone.now()

    filters = {
        "topic__chat": chat,
        "start_at__gte": now,
    }
    if topic is not None:
        filters["topic"] = topic

    return list(
        Meeting.objects.filter(**filters)
        .select_related("topic", "topic__chat")
        .prefetch_related("participants")
        .order_by("start_at", "id")
        .distinct()
    )


def _format_participants(meeting: Meeting) -> str:
    participants = list(meeting.participants.all())
    if not participants:
        return "Все участники"

    names = []
    for p in participants:
        if p.username:
            names.append(f"@{p.username}")
        elif p.full_name:
            names.append(p.full_name)
        else:
            names.append(f"id={p.id}")

    return ", ".join(names)


def _format_meeting_time(meeting: Meeting) -> str:
    dt = meeting.start_at
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime("%d.%m.%Y %H:%M")


@router.message(Command("meetings"))
async def cmd_meetings(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    if message.chat.type == "private":
        meetings = await sync_to_async(_get_upcoming_meetings_for_private)(db_user, chat)
        header = "📅 Ваши встречи:"
    else:
        if not chat:
            await message.answer("Не удалось определить чат.")
            return

        meetings = await sync_to_async(_get_upcoming_meetings_for_chat)(chat, topic)
        header = f"📅 Встречи чата {chat.title}:"

    if not meetings:
        await message.answer("Нет предстоящих встреч.")
        return

    lines = [header, ""]
    for m in meetings:
        mentions = _format_participants(m)
        local_time = _format_meeting_time(m)
        title = (m.title or "Без названия").strip()

        lines.append(
            f"• {title}\n"
            f"  ⏰ {local_time}\n"
            f"  👥 {mentions}\n"
        )

    await message.answer("\n".join(lines))