import html
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.utils import get_chat_context
from core.models import Topic, Summary
from core.services.summary_service import SummaryService

logger = logging.getLogger(__name__)

router = Router()
summary_service = SummaryService()


def _make_aware(dt: datetime) -> datetime:
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _display_period_end(period_end: datetime):
    """
    period_end хранится как правая граница периода [start, end),
    поэтому если это ровно 00:00 следующего дня — показываем предыдущую дату.
    """
    local_end = timezone.localtime(period_end) if timezone.is_aware(period_end) else period_end
    if (
        local_end.hour == 0
        and local_end.minute == 0
        and local_end.second == 0
        and local_end.microsecond == 0
    ):
        return (local_end - timedelta(days=1)).date()
    return local_end.date()


async def send_summary_response(message: Message, summary: Summary):
    start_date = timezone.localtime(summary.period_start).date() if timezone.is_aware(summary.period_start) else summary.period_start.date()
    end_date = _display_period_end(summary.period_end)

    header = f"📊 <b>Саммари за период {start_date} — {end_date}</b>"
    content = html.escape(summary.content or "")

    full_text = f"{header}\n\n{content}"

    # Telegram limit ~4096, оставляем запас
    chunk_size = 3800

    if len(full_text) <= chunk_size:
        await message.answer(full_text, parse_mode="HTML")
        return

    await message.answer(header, parse_mode="HTML")
    for i in range(0, len(content), chunk_size):
        await message.answer(content[i:i + chunk_size], parse_mode="HTML")


def _get_existing_summary(topic: Topic, period_start: datetime, period_end: datetime) -> Optional[Summary]:
    return Summary.objects.filter(
        topic=topic,
        period_start=period_start,
        period_end=period_end,
    ).first()


def _get_or_create_default_topic(chat) -> Tuple[Topic, bool]:
    return Topic.objects.get_or_create(
        chat=chat,
        thread_id=0,
        defaults={"is_active": True},
    )


def _resolve_period(args: list[str], now: datetime) -> Tuple[Optional[datetime], Optional[datetime], Optional[str]]:
    """
    Возвращает:
    start, end, error_message
    где end — правая граница периода [start, end)
    """
    period = args[1].lower()

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end, None

    if period == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end, None

    if period == "week":
        today = timezone.localdate()
        start_of_this_week = today - timedelta(days=today.weekday())
        start_date = start_of_this_week - timedelta(days=7)
        end_date = start_date + timedelta(days=7)

        start = _make_aware(datetime.combine(start_date, datetime.min.time()))
        end = _make_aware(datetime.combine(end_date, datetime.min.time()))
        return start, end, None

    if len(args) < 3:
        return None, None, "Укажите начальную и конечную дату: /summary 2025-01-01 2025-01-07"

    try:
        start_date = datetime.strptime(args[1], "%Y-%m-%d").date()
        end_date_inclusive = datetime.strptime(args[2], "%Y-%m-%d").date()
    except ValueError:
        return None, None, "Неверный формат даты. Используйте YYYY-MM-DD"

    if start_date > end_date_inclusive:
        start_date, end_date_inclusive = end_date_inclusive, start_date

    start = _make_aware(datetime.combine(start_date, datetime.min.time()))
    end = _make_aware(datetime.combine(end_date_inclusive + timedelta(days=1), datetime.min.time()))
    return start, end, None


@router.message(Command("summary"))
async def cmd_summary(message: Message):
    chat, topic, db_user = await get_chat_context(message)

    if not chat:
        await message.answer("Не удалось определить чат.")
        return

    raw_text = message.text or ""
    args = raw_text.split()

    if len(args) < 2:
        await message.answer(
            "Используйте:\n"
            "/summary today — за сегодня\n"
            "/summary yesterday — за вчера\n"
            "/summary week — за прошлую неделю\n"
            "/summary YYYY-MM-DD YYYY-MM-DD — за период"
        )
        return

    try:
        target_topic = topic
        if not target_topic:
            target_topic, _ = await sync_to_async(_get_or_create_default_topic)(chat)

        now = timezone.now()
        start, end, error_text = _resolve_period(args, now)
        if error_text:
            await message.answer(error_text)
            return

        existing = await sync_to_async(_get_existing_summary)(target_topic, start, end)
        if existing:
            await send_summary_response(message, existing)
            return

        await message.answer("⏳ Генерирую саммари, это может занять некоторое время...")

        summary = await summary_service.generate_summary_for_period(target_topic, start, end)
        if summary:
            await send_summary_response(message, summary)
        else:
            await message.answer("Не удалось сгенерировать саммари. Возможно, нет сообщений за этот период.")

    except Exception:
        logger.exception("Summary generation failed")
        await message.answer("⚠️ Ошибка при генерации саммари. Попробуйте позже.")