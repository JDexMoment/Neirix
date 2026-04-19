import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from django.utils import timezone
from django.db.models import Q
from asgiref.sync import sync_to_async
from core.models import Meeting
from bot.utils import get_chat_context

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("meetings"))
async def cmd_meetings(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    now = timezone.now()

    if message.chat.type == "private":
        # Личка: встречи, где пользователь участник, плюс встречи привязанного чата
        def fetch_func():
            return list(
                Meeting.objects.filter(
                    Q(participants=db_user) | Q(topic__chat=chat),
                    start_at__gte=now
                )
                .order_by('start_at')
                .select_related('topic__chat')
                .prefetch_related('participants')
            )
        header = "📅 Ваши встречи (личные и из чата):\n"
    else:
        # Группа: только встречи этого чата
        def fetch_func():
            return list(
                Meeting.objects.filter(
                    topic__chat=chat,
                    start_at__gte=now
                )
                .order_by('start_at')
                .prefetch_related('participants')
            )
        header = f"📅 Встречи чата {chat.title}:\n"

    meetings = await sync_to_async(fetch_func)()

    if not meetings:
        await message.answer("Нет предстоящих встреч.")
        return

    lines = [header]
    for m in meetings:
        p_list = []
        for p in m.participants.all():
            name = f"@{p.username}" if p.username else p.full_name
            p_list.append(name)
        mentions = ", ".join(p_list) if p_list else "Все участники"
        local_time = timezone.localtime(m.start_at).strftime('%d.%m.%Y %H:%M')
        lines.append(
            f"• {m.title}\n"
            f"  ⏰ {local_time}\n"
            f"  👥 {mentions}\n"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")