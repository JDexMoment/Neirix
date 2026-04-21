import asyncio
from datetime import datetime, timedelta
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from django.utils import timezone
from asgiref.sync import sync_to_async
from core.models import Topic, Summary
from core.services.summary_service import SummaryService
from bot.utils import get_chat_context

router = Router()
summary_service = SummaryService()


async def send_summary_response(message: Message, summary: Summary):
    text = (
        f"📊 <b>Саммари за период {summary.period_start.date()} — {summary.period_end.date()}</b>\n\n"
        f"{summary.content}"
    )
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i+4000], parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


@router.message(Command("summary"))
async def cmd_summary(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not chat:
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Используйте:\n"
            "/summary today — за сегодня\n"
            "/summary yesterday — за вчера\n"
            "/summary week — за прошлую неделю\n"
            "/summary YYYY-MM-DD YYYY-MM-DD — за период"
        )
        return

    # Определяем целевую тему
    target_topic = topic
    if not target_topic:
        # Для обычной группы без тем создаём дефолтную тему с thread_id=0
        target_topic, _ = await sync_to_async(Topic.objects.get_or_create)(
            chat=chat,
            thread_id=0,
            defaults={'is_active': True}
        )

    period = args[1].lower()
    now = timezone.now()

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "week":
        today = now.date()
        start_of_this_week = today - timedelta(days=today.weekday())
        start = datetime.combine(start_of_this_week - timedelta(days=7), datetime.min.time())
        end = start + timedelta(days=7)
    else:
        if len(args) >= 3:
            try:
                start = datetime.strptime(args[1], "%Y-%m-%d")
                end = datetime.strptime(args[2], "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                await message.answer("Неверный формат даты. Используйте YYYY-MM-DD")
                return
        else:
            await message.answer("Укажите начальную и конечную дату: /summary 2025-01-01 2025-01-07")
            return

    # Проверяем существующее саммари
    existing = await sync_to_async(Summary.objects.filter(
        topic=target_topic,
        period_start__date=start.date(),
        period_end__date=end.date()
    ).first)()

    if existing:
        await send_summary_response(message, existing)
        return

    await message.answer("⏳ Генерирую саммари, это может занять некоторое время...")
    try:
        summary = await summary_service.generate_summary_for_period(target_topic, start, end)
        if summary:
            await send_summary_response(message, summary)
        else:
            await message.answer("Не удалось сгенерировать саммари. Возможно, нет сообщений за этот период.")
    except Exception as e:
        await message.answer(f"Ошибка при генерации саммари: {str(e)}")