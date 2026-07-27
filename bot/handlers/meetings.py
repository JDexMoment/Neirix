import logging
import re
from datetime import datetime, timedelta
import time
from typing import List, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.states import RescheduleMeetingStates, EditMeetingStates
from bot.utils import get_chat_context
from core.models import Meeting, MeetingRecurrence
from core.services.meeting_service import MeetingService, _is_bot_user
from core.services.message_buffer import MessageBuffer
from core.services.batch_processor import BatchProcessor

from bot.keyboards.inline import (
    meeting_keyboard,
    meeting_edit_options_keyboard,
    meeting_edit_cancel_keyboard,
    meeting_cancel_confirm_keyboard,
    meeting_cancel_series_confirm_keyboard,
    meeting_cancel_choice_keyboard,
    meeting_edit_series_choice_keyboard,
    meeting_reschedule_cancel_keyboard,
)

# For meeting_keyboard import inside sync function
from bot.keyboards.inline import meeting_keyboard as _meeting_keyboard
from core.services.recurrence_service import RecurrenceService

recurrence_service = RecurrenceService()

logger = logging.getLogger(__name__)
router = Router()
meeting_service = MeetingService()


def _get_upcoming_meetings_for_private(db_user, chat=None) -> List[Meeting]:
    from django.db.models import Q
    now = timezone.now()
    query = Q(participants=db_user)
    if chat is not None:
        query |= Q(topic__chat=chat, is_all_hands=True)
    return list(
        Meeting.objects.filter(query, start_at__gte=now, status='active')
        .select_related("topic", "topic__chat", "creator", "recurrence")
        .prefetch_related("participants")
        .order_by("start_at", "id")
        .distinct()
    )


def _get_upcoming_meetings_for_chat(chat, topic=None) -> List[Meeting]:
    now = timezone.now()
    filters = {"topic__chat": chat, "start_at__gte": now, "status": "active"}
    if topic is not None:
        filters["topic"] = topic
    return list(
        Meeting.objects.filter(**filters)
        .select_related("topic", "topic__chat", "creator", "recurrence")
        .prefetch_related("participants")
        .order_by("start_at", "id")
        .distinct()
    )


async def _load_attendance_icons(meetings: list) -> dict:
    """Загружает статусы подтверждения для списка встреч.
    Возвращает {meeting_id: {user_id: icon}}.
    Если на пользователя нет записи — ⏳ (не ответил). """
    if not meetings:
        return {}
    from core.models import MeetingAttendance
    meeting_ids = [m.id for m in meetings]
    
    # Загружаем всех участников встреч
    all_participants = {}
    for m in meetings:
        p_ids = await sync_to_async(lambda m=m: list(m.participants.values_list('id', flat=True)))()
        all_participants[m.id] = {pid: '⏳' for pid in p_ids}
    
    # Загружаем ответивших
    attendances = await sync_to_async(
        lambda: list(MeetingAttendance.objects.filter(meeting_id__in=meeting_ids))
    )()
    for a in attendances:
        icon = "✅" if a.status == "confirmed" else "❌"
        if a.meeting_id in all_participants and a.user_id in all_participants[a.meeting_id]:
            all_participants[a.meeting_id][a.user_id] = icon
    
    return all_participants


def _format_participants(meeting: Meeting, attendance_icons: dict = None) -> str:
    participants = list(meeting.participants.all())
    if not participants:
        if getattr(meeting, 'is_all_hands', False):
            return "Все участники"
        return "не определены"
    names = []
    for p in participants:
        icon = ""
        if attendance_icons:
            icon = attendance_icons.get(p.id, "") or attendance_icons.get(p.id, "")
        name = f"@{p.username}" if p.username else (p.full_name or f"id={p.id}")
        if icon:
            names.append(f"{icon}{name}")
        else:
            names.append(name)
    return ", ".join(names)


def _format_meeting_time(meeting: Meeting) -> str:
    dt = meeting.start_at
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime("%d.%m.%Y %H:%M")


def _format_creator(creator) -> str:
    if not creator:
        return "неизвестен"
    if creator.username:
        return f"@{creator.username}"
    return creator.full_name or f"id={creator.id}"


def _build_meeting_text(meeting: Meeting, attendance_icons: dict = None) -> str:
    text = (
        f"• <b>{meeting.title}</b>\n"
        f"  ⏰ {_format_meeting_time(meeting)}\n"
        f"  👥 {_format_participants(meeting, attendance_icons)}\n"
        f"  📝 Назначил(а): {_format_creator(meeting.creator)}"
    )
    if getattr(meeting, 'recurrence_id', None):
        text += f"\n  🔄 {meeting.recurrence.human_readable}"
    return text


def _build_meeting_text_sync(meeting: Meeting, meeting_id: int):
    """Синхронная версия — все ORM запросы внутри sync_to_async."""
    from core.models import Meeting as MeetingModel
    m = MeetingModel.objects.filter(id=meeting_id).select_related("topic", "topic__chat", "creator").prefetch_related("participants").first()
    if not m:
        return _build_meeting_text(meeting), _meeting_keyboard(meeting_id)

    participants = list(m.participants.all())
    if not participants:
        if getattr(m, 'is_all_hands', False):
            p_str = "Все участники"
        else:
            p_str = "не определены"
    else:
        names = []
        for p in participants:
            if p.username:
                names.append(f"@{p.username}")
            elif p.full_name:
                names.append(p.full_name)
            else:
                names.append(f"id={p.id}")
        p_str = ", ".join(names)

    dt = m.start_at
    from django.utils import timezone as tz
    if tz.is_aware(dt):
        dt = tz.localtime(dt)
    time_str = dt.strftime("%d.%m.%Y %H:%M")

    creator = m.creator
    if not creator:
        c_str = "неизвестен"
    elif creator.username:
        c_str = f"@{creator.username}"
    else:
        c_str = creator.full_name or f"id={creator.id}"

    text = (
        f"• <b>{m.title}</b>\n"
        f"  ⏰ {time_str}\n"
        f"  👥 {p_str}\n"
        f"  📝 Назначил(а): {c_str}"
    )
    if getattr(m, 'recurrence_id', None):
        text += f"\n  🔄 {m.recurrence.human_readable}"
    return text, _meeting_keyboard(meeting_id)


def _parse_user_datetime(text: str) -> Optional[datetime]:
    text = text.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                dt = dt.replace(hour=9, minute=0)
            return dt
        except ValueError:
            continue
    return None


def _parse_meeting_filters(text: str) -> dict:
    """Парсит фильтры из текста команды /meetings ..."""
    filters = {}
    now = timezone.localtime(timezone.now())
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    tomorrow_start = today_start + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)
    parts = text.lower().split()

    for part in parts:
        if part in ("today", "сегодня"):
            filters["start_at__gte"] = today_start
            filters["start_at__lt"] = today_end
            filters["header_suffix"] = "на сегодня"
        elif part in ("tomorrow", "завтра"):
            filters["start_at__gte"] = tomorrow_start
            filters["start_at__lt"] = tomorrow_end
            filters["header_suffix"] = "на завтра"
        elif part in ("week", "неделя", "эту неделю"):
            filters["start_at__gte"] = today_start
            filters["start_at__lt"] = week_end
            filters["header_suffix"] = "на эту неделю"
        elif part.startswith("@"):
            filters["participant_username"] = part.lstrip("@")
        elif part in ("my", "мои", "моё"):
            filters["my"] = True

    return filters


def _apply_meeting_filters(meetings: list, filters: dict, user) -> list:
    """Применяет фильтры к списку встреч."""
    result = []
    now = timezone.now()
    for m in meetings:
        include = True

        # Date range
        start_from = filters.get("start_at__gte")
        start_to = filters.get("start_at__lt")
        if start_from and m.start_at < start_from:
            include = False
        if start_to and m.start_at >= start_to:
            include = False

        # @username
        username = filters.get("participant_username")
        if username:
            participants = list(m.participants.all())
            found = False
            for p in participants:
                if p.username and p.username.lower() == username.lower():
                    found = True
                    break
            if not found:
                include = False

        # My meetings
        if filters.get("my") and user not in list(m.participants.all()):
            include = False

        if include:
            result.append(m)

    return result


async def _respond_meetings(message: Message, chat, topic, db_user, nlp_filters: dict):
    """Отвечает пользователю списком встреч с учётом фильтров."""
    buffer = MessageBuffer()
    processor = BatchProcessor()
    chat_id = message.chat.id
    topic_id = message.message_thread_id if getattr(message.chat, 'is_forum', False) else 0

    pending_messages = buffer.flush(chat_id, topic_id)
    if pending_messages:
        result = await processor.process_batch(chat_id, topic_id, pending_messages)
        unassigned_task_ids = result.get("unassigned_task_ids", [])
        if unassigned_task_ids:
            from core.services.task_service import TaskService
            task_svc = TaskService()
            for task_id in unassigned_task_ids:
                task = await task_svc.get_task_by_id(task_id)
                if task:
                    await task_svc.mark_unassigned(task)
        unassigned_meeting_ids = result.get("unassigned_meeting_ids", [])
        if unassigned_meeting_ids:
            from celery_app.tasks.send_reminders import send_meeting_without_participants_notification
            for meeting_id in unassigned_meeting_ids:
                send_meeting_without_participants_notification.delay(meeting_id)

    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    # Convert NLP filters to meeting filters format
    m_filters = {}
    if nlp_filters.get("today"):
        now = timezone.localtime(timezone.now())
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        m_filters["start_at__gte"] = day_start
        m_filters["start_at__lt"] = day_start + timedelta(days=1)
        m_filters["header_suffix"] = "на сегодня"
    elif nlp_filters.get("tomorrow"):
        now = timezone.localtime(timezone.now())
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = day_start + timedelta(days=1)
        m_filters["start_at__gte"] = tomorrow
        m_filters["start_at__lt"] = tomorrow + timedelta(days=1)
        m_filters["header_suffix"] = "на завтра"
    elif nlp_filters.get("week"):
        now = timezone.localtime(timezone.now())
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        m_filters["start_at__gte"] = day_start
        m_filters["start_at__lt"] = day_start + timedelta(days=7)
        m_filters["header_suffix"] = "на эту неделю"

    if nlp_filters.get("username"):
        m_filters["participant_username"] = nlp_filters["username"]
    if nlp_filters.get("my"):
        m_filters["my"] = True

    if message.chat.type == "private":
        meetings = await sync_to_async(_get_upcoming_meetings_for_private)(db_user, chat)
        base_header = "📅 Ваши встречи"
    else:
        if not chat:
            await message.answer("Не удалось определить чат.")
            return
        meetings = await sync_to_async(_get_upcoming_meetings_for_chat)(chat, topic)
        base_header = f"📅 Встречи чата {chat.title}"

    if m_filters:
        meetings = _apply_meeting_filters(meetings, m_filters, db_user)

    suffix = m_filters.get("header_suffix", "")
    header = f"{base_header} {suffix}:".strip() if suffix else f"{base_header}:"

    if not meetings:
        await message.answer("Нет встреч, соответствующих фильтру.")
        return

    await message.answer(header)
    # ═══ Показываем только одну ближайшую встречу из каждой серии ═══
    meetings = _filter_recurring_meetings(meetings)
    # ═══ Загружаем статусы подтверждения ═══
    attendance_data = await _load_attendance_icons(meetings)
    from bot.handlers.comments import _get_comment_counts
    meeting_ids = [m.id for m in meetings]
    comment_counts = await _get_comment_counts(meeting_ids=meeting_ids)
    for m in meetings:
        has_rec = _has_recurrence(m)
        icons = attendance_data.get(m.id)
        c_count = comment_counts.get(m.id, 0)
        await message.answer(
            _build_meeting_text(m, icons),
            parse_mode="HTML",
            reply_markup=meeting_keyboard(m.id, has_recurrence=has_rec, comment_count=c_count),
        )


async def _handle_nlp_meeting_query(message: Message, intent_type: str, filters: dict):
    """Обрабатывает NLP-запрос о встречах из личных сообщений."""
    from bot.utils import get_chat_context as _get_ctx
    chat, topic, db_user = await _get_ctx(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return
    await _respond_meetings(message, chat, topic, db_user, filters)


def _has_recurrence(meeting: Meeting) -> bool:
    """Проверяет, является ли встреча частью повторяющейся серии."""
    return bool(getattr(meeting, 'recurrence_id', None))


def _filter_recurring_meetings(meetings: list) -> list:
    """Оставляет только одну ближайшую встречу из каждой серии."""
    seen_recurrence_ids = set()
    result = []
    for m in meetings:
        rid = getattr(m, 'recurrence_id', None)
        if rid:
            if rid in seen_recurrence_ids:
                continue
            seen_recurrence_ids.add(rid)
        result.append(m)
    return result


@router.message(Command("meetings"))
async def cmd_meetings(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    text = message.text or ""
    m_filters = _parse_meeting_filters(text)
    await _respond_meetings(message, chat, topic, db_user, m_filters)


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

    # Если встреча часть серии — показываем выбор
    if _has_recurrence(meeting):
        rec = await sync_to_async(lambda: meeting.recurrence)()
        rec_text = rec.human_readable if rec else 'серия'
        rec_id = rec.id if rec else 0
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(
            f"❓ <b>{meeting.title}</b> — это повторяющаяся встреча "
            f"(<i>{rec_text}</i>).\n\n"
            f"Что вы хотите сделать?",
            parse_mode="HTML",
            reply_markup=meeting_cancel_choice_keyboard(
                meeting_id, rec_id,
            ),
        )
        await callback.answer()
        return

    # Обычная встреча — стандартное подтверждение
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
    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    meeting_title = meeting.title if meeting else "Встреча"
    success = await meeting_service.cancel_meeting(meeting_id)
    if success:
        await callback.answer("Встреча отменена.")
        if meeting:
            from celery_app.tasks.send_reminders import send_meeting_cancelled_notification
            send_meeting_cancelled_notification.delay(meeting_id)
        try:
            await callback.message.delete()
        except Exception:
            pass
        try:
            if callback.message.reply_to_message:
                await callback.message.reply_to_message.delete()
        except Exception:
            pass
    else:
        await callback.answer("Не удалось отменить встречу.", show_alert=True)


@router.callback_query(F.data.startswith("meeting_cancel_abort:"))
async def callback_meeting_cancel_abort(callback: CallbackQuery):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка.")
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    try:
        if callback.message.reply_to_message:
            meeting = await meeting_service.get_meeting_by_id(meeting_id)
            if meeting:
                text, markup = await sync_to_async(_build_meeting_text_sync)(meeting, meeting_id)
                await callback.bot.edit_message_text(
                    text,
                    chat_id=callback.message.reply_to_message.chat.id,
                    message_id=callback.message.reply_to_message.message_id,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
    except Exception as e:
        logger.warning("Failed to restore meeting on cancel abort: %s", e)
    await callback.answer("Встреча оставлена без изменений.")


# ── Перенос встречи ──

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

    await state.set_state(RescheduleMeetingStates.waiting_for_new_datetime)
    await state.update_data(
        reschedule_meeting_id=meeting_id,
        reschedule_meeting_title=meeting.title,
        reschedule_old_time=_format_meeting_time(meeting),
        reschedule_original_chat_id=callback.message.chat.id,
        reschedule_original_msg_id=callback.message.message_id,
        _fsm_started_at=time.time(),
    )

    prompt_msg = await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text=f"📅 Перенос встречи <b>{meeting.title}</b>\n\n"
             f"Текущее время: {_format_meeting_time(meeting)}\n\n"
             f"Введите новую дату и время в формате:\n"
             f"<code>25.05.2026 14:00</code>\n\n"
             f"Или напишите <b>отмена</b> для отмены переноса.\n\n"
             f"⏱ У вас есть 2 минуты на ответ.",
        parse_mode="HTML",
        reply_markup=meeting_reschedule_cancel_keyboard(),
    )
    await state.update_data(
        reschedule_prompt_chat_id=prompt_msg.chat.id,
        reschedule_prompt_msg_id=prompt_msg.message_id,
    )
    await callback.answer()


@router.message(RescheduleMeetingStates.waiting_for_new_datetime)
async def process_reschedule_datetime(message: Message, state: FSMContext):
    data = await state.get_data()
    user_text = (message.text or "").strip()

    if user_text.lower() in ("отмена", "cancel", "отменить", "/cancel"):
        prompt_chat = data.get("reschedule_prompt_chat_id")
        prompt_msg = data.get("reschedule_prompt_msg_id")
        if prompt_chat and prompt_msg:
            try:
                await message.bot.delete_message(prompt_chat, prompt_msg)
            except Exception:
                pass
        try:
            await message.delete()
        except Exception:
            pass
        await state.clear()
        await _restore_meeting(message, data)
        return

    new_dt = _parse_user_datetime(user_text)
    if not new_dt:
        await message.answer(
            "❌ Не удалось распознать дату.\n\n"
            "Введите в формате: <code>25.05.2026 14:00</code>\n"
            "Или напишите <b>отмена</b>.",
            parse_mode="HTML",
        )
        return

    current_tz = timezone.get_current_timezone()
    new_start_at = timezone.make_aware(new_dt, current_tz)

    if new_start_at <= timezone.now():
        await message.answer(
            "❌ Дата должна быть в будущем. Попробуйте ещё раз.\n"
            "Или напишите <b>отмена</b>.",
            parse_mode="HTML",
        )
        return

    meeting_id = data.get("reschedule_meeting_id")
    meeting_title = data.get("reschedule_meeting_title", "")
    old_time = data.get("reschedule_old_time", "")

    if not meeting_id:
        await state.clear()
        await message.answer("⚠️ Ошибка: данные потеряны. Попробуйте заново.")
        return

    updated_meeting = await meeting_service.reschedule_meeting(meeting_id, new_start_at)
    await state.clear()

    if updated_meeting:
        new_time_str = timezone.localtime(new_start_at).strftime("%d.%m.%Y %H:%M")

        orig_chat = data.get("reschedule_original_chat_id")
        orig_msg = data.get("reschedule_original_msg_id")
        if orig_chat and orig_msg:
            meeting = await meeting_service.get_meeting_by_id(meeting_id)
            if meeting:
                try:
                    text, markup = await sync_to_async(_build_meeting_text_sync)(meeting, meeting_id)
                    await message.bot.edit_message_text(
                        text,
                        chat_id=orig_chat,
                        message_id=orig_msg,
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
                except Exception as e:
                    logger.warning("Failed to edit rescheduled meeting: %s", e)

        prompt_chat = data.get("reschedule_prompt_chat_id")
        prompt_msg = data.get("reschedule_prompt_msg_id")
        if prompt_chat and prompt_msg:
            try:
                await message.bot.delete_message(prompt_chat, prompt_msg)
            except Exception:
                pass

        try:
            await message.delete()
        except Exception:
            pass

        await message.answer(
            f"✅ Время встречи <b>{meeting_title}</b> изменено:\n"
            f"{old_time} → {new_time_str}",
            parse_mode="HTML",
        )

        if updated_meeting:
            _notify_meeting_changed(updated_meeting, f"⏰ Время: {old_time} → {new_time_str}")
    else:
        await message.answer("❌ Не удалось перенести встречу. Возможно, она была удалена.")


@router.callback_query(F.data == "meeting_reschedule_cancel")
async def callback_reschedule_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _restore_meeting_from_callback(callback, data)
    await callback.answer("Перенос отменён.")


# ── Редактирование встречи ──

@router.callback_query(F.data.startswith("meeting_edit:"))
async def callback_meeting_edit(callback: CallbackQuery, state: FSMContext):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return
    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    if not meeting:
        await callback.answer("Встреча не найдена.", show_alert=True)
        return

    # ═══ Если встреча часть серии — показываем выбор ═══
    if _has_recurrence(meeting):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(
            f"❓ <b>{meeting.title}</b> — это повторяющаяся встреча.\n\n"
            f"Что редактировать?",
            parse_mode="HTML",
            reply_markup=meeting_edit_series_choice_keyboard(meeting_id),
        )
        await callback.answer()
        return

    helper = await callback.message.answer(
        f"✏️ <b>{meeting.title}</b>\n"
        f"  ⏰ {_format_meeting_time(meeting)}\n"
        f"  👥 {_format_participants(meeting)}\n\n"
        f"Что вы хотите изменить?",
        parse_mode="HTML",
        reply_markup=meeting_edit_options_keyboard(meeting_id),
    )
    await callback.answer()

    await state.set_state(EditMeetingStates.waiting_for_choice)
    await state.update_data(
        edit_meeting_id=meeting_id,
        edit_original_chat_id=callback.message.chat.id,
        edit_original_msg_id=callback.message.message_id,
        edit_helper_chat_id=helper.chat.id,
        edit_helper_msg_id=helper.message_id,
    )


@router.callback_query(F.data.startswith("meeting_edit_single:"))
async def callback_meeting_edit_single(callback: CallbackQuery, state: FSMContext):
    """Редактировать ТОЛЬКО ЭТУ встречу (не серию)."""
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return
    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    if not meeting:
        await callback.answer("Встреча не найдена.", show_alert=True)
        return

    helper = await callback.message.answer(
        f"✏️ <b>{meeting.title}</b>\n"
        f"  ⏰ {_format_meeting_time(meeting)}\n"
        f"  👥 {_format_participants(meeting)}\n\n"
        f"Что вы хотите изменить? (только эта встреча)",
        parse_mode="HTML",
        reply_markup=meeting_edit_options_keyboard(meeting_id),
    )
    await callback.answer()

    await state.set_state(EditMeetingStates.waiting_for_choice)
    await state.update_data(
        edit_meeting_id=meeting_id,
        edit_original_chat_id=callback.message.chat.id,
        edit_original_msg_id=callback.message.message_id,
        edit_helper_chat_id=helper.chat.id,
        edit_helper_msg_id=helper.message_id,
    )


@router.callback_query(F.data.startswith("meeting_back:"))
async def callback_meeting_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _restore_meeting_from_callback(callback, data)
    await callback.answer()


@router.callback_query(F.data.startswith("meeting_edit_participants:"))
async def callback_meeting_edit_participants(callback: CallbackQuery, state: FSMContext):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    old_participants = _format_participants(meeting) if meeting else ""
    await state.update_data(edit_old_participants=old_participants)

    await state.set_state(EditMeetingStates.waiting_for_participants)
    await callback.message.edit_text(
        "👤 Напишите @username участников (через пробел).\n\n"
        f"Текущие: {old_participants}\n\n"
        "<i>(все предыдущие участники будут заменены)</i>\n\n"
        "Или нажмите кнопку отмены.",
        parse_mode="HTML",
        reply_markup=meeting_edit_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("meeting_edit_title:"))
async def callback_meeting_edit_title(callback: CallbackQuery, state: FSMContext):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    old_title = meeting.title if meeting else ""
    await state.update_data(edit_old_title=old_title)

    await state.set_state(EditMeetingStates.waiting_for_title)
    await callback.message.edit_text(
        f"✏️ Введите новое название встречи.\n\n"
        f"Текущее: <b>{old_title}</b>\n\n"
        "Или нажмите кнопку отмены.",
        parse_mode="HTML",
        reply_markup=meeting_edit_cancel_keyboard(),
    )
    await callback.answer()


def _should_apply_to_series(state_data: dict) -> bool:
    return state_data.get("edit_is_series", False)


@router.message(EditMeetingStates.waiting_for_title)
async def process_edit_meeting_title(message: Message, state: FSMContext):
    data = await state.get_data()
    meeting_id = data.get("edit_meeting_id")
    if not meeting_id:
        await state.clear()
        await message.answer("⚠️ Ошибка: данные потеряны. Попробуйте заново.")
        return

    new_title = message.text.strip()
    if new_title.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer("↩️ Редактирование отменено.")
        return

    if not new_title:
        await message.answer("❌ Название не может быть пустым.")
        return

    old_title = data.get("edit_old_title", "")
    success = await meeting_service.update_title(meeting_id, new_title)
    if _should_apply_to_series(data) and success:
        meeting = await meeting_service.get_meeting_by_id(meeting_id)
        if meeting and meeting.recurrence_id:
            rec = await sync_to_async(MeetingRecurrence.objects.get)(id=meeting.recurrence_id)
            await recurrence_service.update_series_title(rec, new_title)
            logger.info("Series title updated for rec_id=%s", meeting.recurrence_id)
    await state.clear()

    if success:
        meeting = await meeting_service.get_meeting_by_id(meeting_id)
        if meeting:
            orig_chat = data.get("edit_original_chat_id")
            orig_msg = data.get("edit_original_msg_id")
            if orig_chat and orig_msg:
                try:
                    text, markup = await sync_to_async(_build_meeting_text_sync)(meeting, meeting_id)
                    await message.bot.edit_message_text(
                        text,
                        chat_id=orig_chat,
                        message_id=orig_msg,
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
                except Exception as e:
                    logger.warning("Failed to edit meeting: %s", e)

            helper_chat = data.get("edit_helper_chat_id")
            helper_msg = data.get("edit_helper_msg_id")
            if helper_chat and helper_msg:
                try:
                    await message.bot.delete_message(helper_chat, helper_msg)
                except Exception:
                    pass

            try:
                await message.delete()
            except Exception:
                pass

            await message.answer(
                f"✅ Название встречи изменено:\n"
                f"{old_title} → {meeting.title}",
                parse_mode="HTML",
            )

            _notify_meeting_changed(meeting, f"✏️ Название: {old_title} → {meeting.title}")
    else:
        await message.answer("❌ Не удалось обновить название.")


@router.callback_query(F.data == "meeting_edit_cancel")
async def callback_meeting_edit_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _restore_meeting_from_callback(callback, data)
    await callback.answer("Редактирование отменено.")


@router.message(EditMeetingStates.waiting_for_participants)
async def process_edit_participants(message: Message, state: FSMContext):
    data = await state.get_data()
    meeting_id = data.get("edit_meeting_id")
    if not meeting_id:
        await state.clear()
        await message.answer("⚠️ Ошибка: данные потеряны. Попробуйте заново.")
        return

    text = message.text.strip()
    if text.lower() in ("отмена", "cancel", "/cancel", "-"):
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer("↩️ Редактирование отменено.")
        return

    usernames = re.findall(r"@\w+", text)
    if not usernames:
        await message.answer(
            "❌ Не найден @username. Напишите в формате <code>@username</code>.\n"
            "Или напишите <b>отмена</b>.",
            parse_mode="HTML",
        )
        return

    old_participants_str = data.get("edit_old_participants", "")
    success = await meeting_service.update_participants(meeting_id, usernames)
    if _should_apply_to_series(data) and success:
        meeting = await meeting_service.get_meeting_by_id(meeting_id)
        if meeting and meeting.recurrence_id:
            rec = await sync_to_async(MeetingRecurrence.objects.get)(id=meeting.recurrence_id)
            await recurrence_service.update_series_participants(rec, usernames)
            logger.info("Series participants updated for rec_id=%s", meeting.recurrence_id)
    await state.clear()

    if success:
        meeting = await meeting_service.get_meeting_by_id(meeting_id)
        if meeting:
            new_participants_str = _format_participants(meeting)

            orig_chat = data.get("edit_original_chat_id")
            orig_msg = data.get("edit_original_msg_id")
            if orig_chat and orig_msg:
                try:
                    text, markup = await sync_to_async(_build_meeting_text_sync)(meeting, meeting_id)
                    await message.bot.edit_message_text(
                        text,
                        chat_id=orig_chat,
                        message_id=orig_msg,
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
                except Exception as e:
                    logger.warning("Failed to edit meeting: %s", e)

            helper_chat = data.get("edit_helper_chat_id")
            helper_msg = data.get("edit_helper_msg_id")
            if helper_chat and helper_msg:
                try:
                    await message.bot.delete_message(helper_chat, helper_msg)
                except Exception:
                    pass

            try:
                await message.delete()
            except Exception:
                pass

            await message.answer(
                f"✅ Участники встречи <b>{meeting.title}</b> изменены:\n"
                f"{old_participants_str} → {new_participants_str}",
                parse_mode="HTML",
            )

            from celery_app.tasks.send_reminders import send_meeting_assigned_notification
            send_meeting_assigned_notification.delay(meeting_id)
    else:
        await message.answer("❌ Не удалось обновить участников.")


# ── Отмена ОДНОЙ встречи из серии ──

@router.callback_query(F.data.startswith("meeting_cancel_single:"))
async def callback_meeting_cancel_single(callback: CallbackQuery):
    """Отменяет только одну встречу (серия продолжается)."""
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    if not meeting:
        await callback.answer("Встреча не найдена.", show_alert=True)
        return

    success = await recurrence_service.cancel_single_meeting(meeting)
    if success:
        await callback.answer("✅ Встреча отменена. Серия продолжается.")
        try:
            await callback.message.delete()
        except Exception:
            pass
        try:
            if callback.message.reply_to_message:
                await callback.message.reply_to_message.delete()
        except Exception:
            pass
    else:
        await callback.answer("❌ Не удалось отменить встречу.", show_alert=True)


# ── Отмена ВСЕЙ серии встреч ──

@router.callback_query(F.data.startswith("meeting_cancel_series:"))
async def callback_meeting_cancel_series(callback: CallbackQuery):
    """Отменяет всю серию повторяющихся встреч."""
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    if not meeting:
        await callback.answer("Встреча не найдена.", show_alert=True)
        return

    recurrence_id = meeting.recurrence_id
    if not recurrence_id:
        await callback.answer("Эта встреча не является частью серии.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(
        f"🛑 <b>Отмена всей серии</b>\n\n"
        f"Вы уверены, что хотите отменить ВСЕ повторения встречи "
        f"<b>{meeting.title}</b>?\n\n"
        f"Все встречи этой серии (включая текущую) будут отменены.",
        parse_mode="HTML",
        reply_markup=meeting_cancel_series_confirm_keyboard(meeting_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("meeting_cancel_series_confirm:"))
async def callback_meeting_cancel_series_confirm(callback: CallbackQuery):
    """Подтверждает отмену всей серии встреч."""
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    if not meeting or not meeting.recurrence_id:
        await callback.answer("Серия не найдена.", show_alert=True)
        return

    success = await recurrence_service.cancel_meeting_series(meeting.recurrence_id)
    if success:
        await callback.answer("✅ Серия встреч отменена!")
        try:
            await callback.message.delete()
        except Exception:
            pass
        try:
            if callback.message.reply_to_message:
                await callback.message.reply_to_message.delete()
        except Exception:
            pass

        await callback.message.answer(
            f"🛑 Серия встреч <b>{meeting.title}</b> отменена.\n\n"
            f"Новые встречи создаваться не будут.",
            parse_mode="HTML",
        )
    else:
        await callback.answer("❌ Не удалось отменить серию.", show_alert=True)


# ── Назад из выбора отмены ──

@router.callback_query(F.data.startswith("meeting_back_single:"))
async def callback_meeting_back_single(callback: CallbackQuery):
    """Возвращает к просмотру встречи (из выбора отмены)."""
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка.")
        return
    await _restore_meeting_from_simple(callback, meeting_id)
    await callback.answer()


async def _restore_meeting_from_simple(callback, meeting_id):
    """Удаляет сообщение (статистику или информацию)."""
    try:
        await callback.message.delete()
    except Exception:
        pass


# ── Редактирование серии встреч ──

@router.callback_query(F.data.startswith("meeting_edit_series:"))
async def callback_meeting_edit_series(callback: CallbackQuery, state: FSMContext):
    """Редактирование всей серии встреч."""
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    meeting = await meeting_service.get_meeting_by_id(meeting_id)
    if not meeting or not meeting.recurrence_id:
        await callback.answer("Эта встреча не является частью серии.", show_alert=True)
        return

    helper = await callback.message.answer(
        f"🔄 <b>Редактирование серии: {meeting.title}</b>\n\n"
        f"Изменения применятся ко ВСЕМ будущим встречам этой серии.\n\n"
        f"Что хотите изменить?",
        parse_mode="HTML",
        reply_markup=meeting_edit_options_keyboard(meeting_id, has_recurrence=True),
    )
    await callback.answer()

    await state.set_state(EditMeetingStates.waiting_for_choice)
    await state.update_data(
        edit_meeting_id=meeting_id,
        edit_original_chat_id=callback.message.chat.id,
        edit_original_msg_id=callback.message.message_id,
        edit_helper_chat_id=helper.chat.id,
        edit_helper_msg_id=helper.message_id,
        edit_is_series=True,
    )


def _notify_meeting_changed(meeting, change_text):
    """Отправляет уведомление участникам об изменении встречи через Celery."""
    from celery_app.tasks.send_reminders import send_meeting_changed_notification
    send_meeting_changed_notification.delay(meeting.id, change_text)


async def _restore_meeting_from_callback(callback, data):
    orig_chat = data.get("edit_original_chat_id") or data.get("reschedule_original_chat_id")
    orig_msg = data.get("edit_original_msg_id") or data.get("reschedule_original_msg_id")
    meeting_id = data.get("edit_meeting_id") or data.get("reschedule_meeting_id")
    if orig_chat and orig_msg and meeting_id:
        meeting = await meeting_service.get_meeting_by_id(meeting_id)
        if meeting:
            try:
                text, markup = await sync_to_async(_build_meeting_text_sync)(meeting, meeting_id)
                await callback.bot.edit_message_text(
                    text,
                    chat_id=orig_chat,
                    message_id=orig_msg,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            except Exception as e:
                logger.warning("Failed to restore meeting: %s", e)


async def _restore_meeting(message, data):
    orig_chat = data.get("reschedule_original_chat_id")
    orig_msg = data.get("reschedule_original_msg_id")
    meeting_id = data.get("reschedule_meeting_id")
    if orig_chat and orig_msg and meeting_id:
        meeting = await meeting_service.get_meeting_by_id(meeting_id)
        if meeting:
            try:
                text, markup = await sync_to_async(_build_meeting_text_sync)(meeting, meeting_id)
                await message.bot.edit_message_text(
                    text,
                    chat_id=orig_chat,
                    message_id=orig_msg,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            except Exception as e:
                logger.warning("Failed to restore meeting: %s", e)
