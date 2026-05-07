import logging
from datetime import datetime
import time
from typing import List, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.states import RescheduleMeetingStates
from bot.utils import get_chat_context
from core.models import Meeting
from core.services.meeting_service import MeetingService

from bot.keyboards.inline import (
    meeting_keyboard,
    meeting_cancel_confirm_keyboard,
    meeting_reschedule_cancel_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()
meeting_service = MeetingService()


# ─────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────────────


def _get_upcoming_meetings_for_private(db_user, chat=None) -> List[Meeting]:
    from django.db.models import Q

    now = timezone.now()
    query = Q(participants=db_user)
    if chat is not None:
        query |= Q(topic__chat=chat)

    return list(
        Meeting.objects.filter(
            query,
            start_at__gte=now,
            status='active',
        )
        .select_related("topic", "topic__chat")
        .prefetch_related("participants")
        .order_by("start_at", "id")
        .distinct()
    )


def _get_upcoming_meetings_for_chat(chat, topic=None) -> List[Meeting]:
    now = timezone.now()
    filters = {
        "topic__chat": chat,
        "start_at__gte": now,
        "status": "active",
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

def _parse_user_datetime(text: str) -> Optional[datetime]:
    """
    Парсит дату/время из пользовательского ввода.
    Поддерживаемые форматы:
    - 25.05.2026 14:00
    - 25.05.2026 14:00:00
    - 2026-05-25 14:00
    - 25.05.2026 (время = 09:00)
    - 2026-05-25 (время = 09:00)
    """
    text = text.strip()
    for fmt in (
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            if fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                dt = dt.replace(hour=9, minute=0)
            return dt
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────
# /meetings — список встреч
# ─────────────────────────────────────────────────────────────────────


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

    await message.answer(header)

    for m in meetings:
        mentions = _format_participants(m)
        local_time = _format_meeting_time(m)
        title = (m.title or "Без названия").strip()

        await message.answer(
            f"• <b>{title}</b>\n"
            f"  ⏰ {local_time}\n"
            f"  👥 {mentions}",
            parse_mode="HTML",
            reply_markup=meeting_keyboard(m.id),
        )


# ─────────────────────────────────────────────────────────────────────
# Отмена встречи
# ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("meeting_cancel:"))
async def callback_meeting_cancel(callback: CallbackQuery):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    if not meeting:
        await callback.answer("Встреча не найдена.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(
        f"Вы уверены, что хотите отменить встречу <b>{meeting.title}</b>?",
        parse_mode="HTML",
        reply_markup=meeting_cancel_confirm_keyboard(meeting_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("meeting_cancel_confirm:"))
async def callback_meeting_cancel_confirm(callback: CallbackQuery):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    success = await meeting_service.cancel_meeting(meeting_id)

    if success:
        await callback.answer("Встреча отменена.")
        try:
            await callback.message.edit_text("❌ Встреча отменена.")
        except Exception:
            await callback.message.reply("❌ Встреча отменена.")
    else:
        await callback.answer("Не удалось отменить встречу.", show_alert=True)


@router.callback_query(F.data.startswith("meeting_cancel_abort:"))
async def callback_meeting_cancel_abort(callback: CallbackQuery):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка.")
        return

    await callback.answer("Встреча оставлена без изменений.")
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n↩️ Отмена отменена.",
            parse_mode="HTML",
            reply_markup=meeting_keyboard(meeting_id),
        )
    except Exception:
        await callback.message.reply(
            "↩️ Встреча оставлена без изменений.",
            reply_markup=meeting_keyboard(meeting_id),
        )


# ─────────────────────────────────────────────────────────────────────
# Перенос встречи — шаг 1: нажатие кнопки
# ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("meeting_reschedule:"))
async def callback_meeting_reschedule(callback: CallbackQuery, state: FSMContext):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    if not meeting:
        await callback.answer("Встреча не найдена.", show_alert=True)
        return

    # Сохраняем ID встречи в FSM
    await state.set_state(RescheduleMeetingStates.waiting_for_new_datetime)
    await state.update_data(
        reschedule_meeting_id=meeting_id,
        reschedule_meeting_title=meeting.title,
        _fsm_started_at=time.time()
    )

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.reply(
        f"📅 Перенос встречи <b>{meeting.title}</b>\n\n"
        f"Текущее время: {_format_meeting_time(meeting)}\n\n"
        f"Введите новую дату и время в формате:\n"
        f"<code>25.05.2026 14:00</code>\n\n"
        f"Или напишите <b>отмена</b> для отмены переноса.\n\n"
        f"⏱ У вас есть 2 минуты на ответ.",
        parse_mode="HTML",
        reply_markup=meeting_reschedule_cancel_keyboard(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────────────
# Перенос встречи — шаг 2: пользователь вводит новую дату
# ─────────────────────────────────────────────────────────────────────


@router.message(RescheduleMeetingStates.waiting_for_new_datetime)
async def process_reschedule_datetime(message: Message, state: FSMContext):
    user_text = (message.text or "").strip()

    # Проверяем отмену
    if user_text.lower() in ("отмена", "cancel", "отменить", "/cancel"):
        await state.clear()
        await message.answer("↩️ Перенос встречи отменён.")
        return

    # Парсим дату
    new_dt = _parse_user_datetime(user_text)
    if not new_dt:
        await message.answer(
            "❌ Не удалось распознать дату.\n\n"
            "Введите в формате: <code>25.05.2026 14:00</code>\n"
            "Или напишите <b>отмена</b>.",
            parse_mode="HTML",
        )
        return

    # Проверяем что дата в будущем
    current_tz = timezone.get_current_timezone()
    new_start_at = timezone.make_aware(new_dt, current_tz)

    if new_start_at <= timezone.now():
        await message.answer(
            "❌ Дата должна быть в будущем. Попробуйте ещё раз.\n"
            "Или напишите <b>отмена</b>.",
            parse_mode="HTML",
        )
        return

    # Получаем данные из FSM
    data = await state.get_data()
    meeting_id = data.get("reschedule_meeting_id")
    meeting_title = data.get("reschedule_meeting_title", "")

    if not meeting_id:
        await state.clear()
        await message.answer("⚠️ Ошибка: данные переноса потеряны. Попробуйте заново.")
        return

    # Выполняем перенос
    updated_meeting = await meeting_service.reschedule_meeting(meeting_id, new_start_at)
    await state.clear()

    if updated_meeting:
        new_time_str = timezone.localtime(new_start_at).strftime("%d.%m.%Y %H:%M")
        await message.answer(
            f"✅ Встреча <b>{meeting_title}</b> перенесена на {new_time_str}.",
            parse_mode="HTML",
        )
    else:
        await message.answer("❌ Не удалось перенести встречу. Возможно, она была удалена.")


# ─────────────────────────────────────────────────────────────────────
# Перенос — отмена через inline-кнопку
# ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "meeting_reschedule_cancel")
async def callback_reschedule_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Перенос отменён.")
    try:
        await callback.message.edit_text("↩️ Перенос встречи отменён.")
    except Exception:
        await callback.message.reply("↩️ Перенос встречи отменён.")