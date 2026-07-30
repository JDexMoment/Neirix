import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from asgiref.sync import sync_to_async
from django.utils import timezone

from core.models import Task, TelegramUser
from core.services.task_service import TaskService, _is_bot_user
from bot.utils import get_chat_context
from bot.keyboards.inline import (
    task_keyboard,
    task_edit_options_keyboard,
    task_edit_cancel_keyboard,
    task_cancel_series_confirm_keyboard,
    task_edit_series_choice_keyboard,
)
from bot.states import EditTaskStates
from core.services.recurrence_service import RecurrenceService

recurrence_service = RecurrenceService()

logger = logging.getLogger(__name__)
router = Router()
task_service = TaskService()


def _get_open_tasks_for_private(db_user: TelegramUser) -> List[Task]:
    return list(
        Task.objects.filter(
            assignees__user=db_user,
            status="open",
        )
        .select_related("topic__chat", "creator")
        .prefetch_related("assignees__user")
        .order_by("due_date", "id")
        .distinct()
    )


def _get_all_tasks_for_private(db_user: TelegramUser) -> List[Task]:
    return list(
        Task.objects.filter(
            assignees__user=db_user,
        )
        .select_related("topic__chat", "creator")
        .prefetch_related("assignees__user")
        .order_by("due_date", "id")
        .distinct()
    )


def _get_open_tasks_for_chat(chat, topic=None) -> List[Task]:
    filters = {"topic__chat": chat, "status": "open"}
    if topic:
        filters["topic"] = topic
    return list(
        Task.objects.filter(**filters)
        .select_related("creator")
        .prefetch_related("assignees__user")
        .order_by("due_date", "id")
        .distinct()
    )


def _get_all_tasks_for_chat(chat, topic=None) -> List[Task]:
    filters = {"topic__chat": chat}
    if topic:
        filters["topic"] = topic
    return list(
        Task.objects.filter(**filters)
        .select_related("creator")
        .prefetch_related("assignees__user")
        .order_by("due_date", "id")
        .distinct()
    )


def _get_telegram_user_by_telegram_id(telegram_id: int) -> Optional[TelegramUser]:
    return TelegramUser.objects.filter(telegram_id=telegram_id).first()


def _format_due_date(task: Task) -> str:
    if not task.due_date:
        return "без срока"
    dt = task.due_date
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    if task.status == "open" and task.due_date < timezone.now():
        diff = timezone.now() - task.due_date
        days = diff.days
        hours = diff.seconds // 3600
        if days > 0:
            return f"🚨 Просрочено на {days}д {hours}ч"
        else:
            return f"🚨 Просрочено на {hours}ч"
    return f"📅 до {dt.strftime('%d.%m.%Y')}"


def _format_assignees(task: Task) -> str:
    assignee_list = [a.user for a in task.assignees.all()]
    if not assignee_list:
        return "не назначен"
    return ", ".join(
        f"@{u.username}" if u.username else (u.full_name or f"id={u.id}")
        for u in assignee_list
    )


def _format_creator(creator) -> str:
    if not creator:
        return "неизвестен"
    if creator.username:
        return f"@{creator.username}"
    return creator.full_name or f"id={creator.id}"


def _get_priority_emoji(task) -> str:
    p = getattr(task, 'priority', 'normal') or 'normal'
    return {
        'critical': '🔴 ',
        'high': '🟡 ',
        'normal': '',
        'low': '⚪ ',
    }.get(p, '')


def _build_task_text(task: Task, recurrence_text: str = None) -> str:
    emoji = _get_priority_emoji(task)
    text = (
        f"{emoji}<b>{task.title}</b>\n"
        f"👤 {_format_assignees(task)}\n"
        f"{_format_due_date(task)}\n"
        f"📝 Назначил(а): {_format_creator(task.creator)}"
    )
    if recurrence_text:
        text += f"\n🔄 {recurrence_text}"
    elif task.recurrence_group_id:
        text += f"\n🔄 Повторяющаяся"
    if task.status == "done" and getattr(task, 'completed_at', None):
        dt = task.completed_at
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        text += f"\n✅ Выполнено: {dt.strftime('%d.%m.%Y %H:%M')}"
    return text


def _parse_task_filters(text: str, user: TelegramUser) -> dict:
    """Парсит фильтры из текста команды /tasks ..."""
    filters = {}
    now = timezone.localtime(timezone.now())
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    tomorrow_start = today_start + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)
    parts = text.lower().split()

    for part in parts:
        if part in ("overdue", "просрочено", "просроченные"):
            filters["status"] = "open"
            filters["due_date__lt"] = now
        elif part in ("today", "сегодня"):
            filters["due_date__gte"] = today_start
            filters["due_date__lt"] = today_end
            filters["header_suffix"] = "на сегодня"
        elif part in ("tomorrow", "завтра"):
            filters["due_date__gte"] = tomorrow_start
            filters["due_date__lt"] = tomorrow_end
            filters["header_suffix"] = "на завтра"
        elif part in ("week", "неделя", "эту неделю"):
            filters["due_date__gte"] = today_start
            filters["due_date__lt"] = week_end
            filters["header_suffix"] = "на эту неделю"
        elif part in ("done", "выполненные", "выполнено"):
            filters["status"] = "done"
        elif part in ("my", "мои", "моё"):
            filters["my"] = True
        elif part in ("important", "важные", "важно", "срочно"):
            filters["important"] = True
        elif part.startswith("@"):
            filters["assignee_username"] = part.lstrip("@")

    return filters


def _apply_task_filters(tasks: list, filters: dict, user: TelegramUser) -> list:
    """Применяет фильтры к списку задач."""
    result = []
    for task in tasks:
        include = True

        # Статус (если не указан — только open)
        status = filters.get("status", "open")
        if task.status != status:
            include = False

        # Due date
        due_from = filters.get("due_date__gte")
        due_to = filters.get("due_date__lt")
        if due_from and (not task.due_date or task.due_date < due_from):
            include = False
        if due_to and (not task.due_date or task.due_date >= due_to):
            include = False
        if "due_date__lt" in filters and not due_to:
            # overdue filter
            if not task.due_date or task.due_date >= filters["due_date__lt"]:
                include = False

        # @username
        username = filters.get("assignee_username")
        if username:
            assignees = [a.user for a in task.assignees.all()]
            found = False
            for a in assignees:
                if a.username and a.username.lower() == username.lower():
                    found = True
                    break
            if not found:
                include = False

        # My tasks (in group)
        if filters.get("my") and user not in [a.user for a in task.assignees.all()]:
            include = False

        # ═══ Фильтр "важные" ═══
        if filters.get("important") and getattr(task, 'priority', 'normal') not in ('critical', 'high'):
            include = False

        if include:
            result.append(task)

    return result


def _filter_recurring_tasks(tasks: list) -> list:
    """Оставляет только одну задачу из каждой серии (ближайшую)."""
    seen_group_ids = set()
    result = []
    for t in tasks:
        gid = t.recurrence_group_id
        if gid:
            if gid in seen_group_ids:
                continue
            seen_group_ids.add(gid)
        result.append(t)
    return result


async def _get_recurrence_text(task: Task) -> str:
    """Загружает human_readable из TaskRecurrence по recurrence_group_id."""
    if not task.recurrence_group_id:
        return None
    from core.models import TaskRecurrence
    rec = await sync_to_async(
        lambda: TaskRecurrence.objects.filter(
            task__recurrence_group_id=task.recurrence_group_id,
            is_active=True
        ).values_list("human_readable", flat=True).first()
    )()
    return rec


async def _respond_tasks(message: Message, chat, topic, db_user, filters: dict):
    """Отвечает пользователю списком задач с учётом фильтров."""
    wants_done = filters.get("status") == "done"

    if message.chat.type == "private":
        if wants_done:
            tasks = await sync_to_async(_get_all_tasks_for_private)(db_user)
        else:
            tasks = await sync_to_async(_get_open_tasks_for_private)(db_user)
        base_header = "📋 Ваши задачи"
    else:
        if wants_done:
            if not filters.get("assignee_username"):
                filters["my"] = True
            tasks = await sync_to_async(_get_all_tasks_for_chat)(chat, topic)
        else:
            tasks = await sync_to_async(_get_open_tasks_for_chat)(chat, topic)
        if not chat:
            await message.answer("Не удалось определить чат.")
            return
        if wants_done:
            base_header = "📋 Мои выполненные задачи"
        else:
            base_header = f"📋 Задачи чата {chat.title}"

    if filters:
        tasks = _apply_task_filters(tasks, filters, db_user)

    # ═══ Сортировка: приоритетные → normal → low, внутри по дате ═══
    priority_order = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3}
    tasks.sort(key=lambda t: (priority_order.get(getattr(t, 'priority', 'normal') or 'normal', 2),
                               getattr(t, 'due_date', None) or timezone.now()))

    suffix = filters.get("header_suffix", "")
    header = f"{base_header} {suffix}:".strip() if suffix else f"{base_header}:"

    if not tasks:
        await message.answer("Нет задач, соответствующих фильтру.")
        return
    await message.answer(header)

    # ═══ Показываем только одну задачу из каждой серии ═══
    if not wants_done:
        tasks = _filter_recurring_tasks(tasks)

    if wants_done:
        lines = []
        total = len(tasks)
        shown = tasks[:10] if total > 10 else tasks
        for i, task in enumerate(shown, 1):
            rec_text = await _get_recurrence_text(task)
            lines.append(f"{i}. {_build_task_text(task, rec_text)}")
            lines.append("")
        text = "\n".join(lines).rstrip("\n")
        if total > 10:
            text += f"\n\n... и ещё {total - 10} выполненных задач"
        await message.answer(text, parse_mode="HTML")
        return

    from bot.handlers.comments import _get_comment_counts
    task_ids = [t.id for t in tasks]
    comment_counts = await _get_comment_counts(task_ids=task_ids)
    for i, task in enumerate(tasks, 1):
        rec_text = await _get_recurrence_text(task)
        has_rec = task.recurrence_group_id is not None
        c_count = comment_counts.get(task.id, 0)
        await message.answer(
            f"{i}. {_build_task_text(task, rec_text)}",
            parse_mode="HTML",
            reply_markup=task_keyboard(task.id, has_recurrence=has_rec, comment_count=c_count),
        )


async def _handle_nlp_query(message: Message, intent_type: str, filters: dict):
    """Обрабатывает NLP-запрос из личных сообщений."""
    from bot.utils import get_chat_context as _get_ctx
    chat, topic, db_user = await _get_ctx(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    # Преобразуем NLP-фильтры в формат _parse_task_filters
    task_filters = {}
    if filters.get("overdue"):
        task_filters["status"] = "open"
        task_filters["due_date__lt"] = timezone.localtime(timezone.now())
    elif filters.get("done"):
        task_filters["status"] = "done"
    if filters.get("today"):
        now = timezone.localtime(timezone.now())
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        task_filters["due_date__gte"] = day_start
        task_filters["due_date__lt"] = day_start + timedelta(days=1)
        task_filters["header_suffix"] = "на сегодня"
    elif filters.get("tomorrow"):
        now = timezone.localtime(timezone.now())
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = day_start + timedelta(days=1)
        task_filters["due_date__gte"] = tomorrow
        task_filters["due_date__lt"] = tomorrow + timedelta(days=1)
        task_filters["header_suffix"] = "на завтра"
    elif filters.get("week"):
        now = timezone.localtime(timezone.now())
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        task_filters["due_date__gte"] = day_start
        task_filters["due_date__lt"] = day_start + timedelta(days=7)
        task_filters["header_suffix"] = "на эту неделю"

    if filters.get("username"):
        task_filters["assignee_username"] = filters["username"]
    if filters.get("my"):
        task_filters["my"] = True
    if filters.get("list_all"):
        pass  # Покажем все

    await _respond_tasks(message, chat, topic, db_user, task_filters)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return
    text = message.text or ""
    filters = _parse_task_filters(text, db_user)
    await _respond_tasks(message, chat, topic, db_user, filters)


@router.callback_query(F.data.startswith("task_done:"))
async def callback_task_done(callback: CallbackQuery):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор задачи.", show_alert=True)
        return
    db_user = await sync_to_async(_get_telegram_user_by_telegram_id)(callback.from_user.id)
    if not db_user:
        await callback.answer("Пользователь не найден в базе.", show_alert=True)
        return
    success = await task_service.mark_task_done(task_id, db_user)
    if success:
        await callback.answer("✅ Задача выполнена!")
        try:
            await callback.message.delete()
        except Exception as e:
            logger.warning("Failed to delete task message: %s", e)
    else:
        await callback.answer("❌ Ошибка: задача не найдена или нет прав.", show_alert=True)


@router.callback_query(F.data.startswith("task_back:"))
async def callback_task_back(callback: CallbackQuery, state: FSMContext):
    """Возвращает к просмотру задачи (удаляет хелпер, восстанавливает оригинал)."""
    data = await state.get_data()
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    orig_chat = data.get("edit_original_chat_id")
    orig_msg = data.get("edit_original_msg_id")
    task_id = data.get("edit_task_id")
    if orig_chat and orig_msg and task_id:
        task = await task_service.get_task_by_id(task_id)
        if task:
            try:
                await callback.bot.edit_message_text(
                    _build_task_text(task),
                    chat_id=orig_chat,
                    message_id=orig_msg,
                    parse_mode="HTML",
                    reply_markup=task_keyboard(task_id),
                )
            except Exception as e:
                logger.warning("Failed to restore task: %s", e)
    await callback.answer()


@router.callback_query(F.data.startswith("task_edit:"))
async def callback_task_edit(callback: CallbackQuery, state: FSMContext):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return
    task = await task_service.get_task_by_id(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    # ═══ Если задача часть серии — показываем выбор ═══
    has_rec = task.recurrence_group_id is not None
    if has_rec:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(
            f"❓ <b>{task.title}</b> — это повторяющаяся задача.\n\n"
            f"Что редактировать?",
            parse_mode="HTML",
            reply_markup=task_edit_series_choice_keyboard(task_id),
        )
        await callback.answer()
        return

    assignee_str = _format_assignees(task)
    due_str = _format_due_date(task)
    old_text = _build_task_text(task)

    helper = await callback.message.answer(
        f"✏️ <b>{task.title}</b>\n"
        f"👤 {assignee_str}\n"
        f"{due_str}\n\n"
        f"Что вы хотите изменить?",
        parse_mode="HTML",
        reply_markup=task_edit_options_keyboard(task_id),
    )
    await callback.answer()

    await state.set_state(EditTaskStates.waiting_for_choice)
    await state.update_data(
        edit_task_id=task_id,
        edit_original_chat_id=callback.message.chat.id,
        edit_original_msg_id=callback.message.message_id,
        edit_helper_chat_id=helper.chat.id,
        edit_helper_msg_id=helper.message_id,
        edit_old_text=old_text,
    )


@router.callback_query(F.data.startswith("task_edit_single:"))
async def callback_task_edit_single(callback: CallbackQuery, state: FSMContext):
    """Редактировать ТОЛЬКО ЭТУ задачу (не серию)."""
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return
    task = await task_service.get_task_by_id(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    assignee_str = _format_assignees(task)
    due_str = _format_due_date(task)
    old_text = _build_task_text(task)

    helper = await callback.message.answer(
        f"✏️ <b>{task.title}</b>\n"
        f"👤 {assignee_str}\n"
        f"{due_str}\n\n"
        f"Что вы хотите изменить? (только эта задача)",
        parse_mode="HTML",
        reply_markup=task_edit_options_keyboard(task_id),
    )
    await callback.answer()

    await state.set_state(EditTaskStates.waiting_for_choice)
    await state.update_data(
        edit_task_id=task_id,
        edit_original_chat_id=callback.message.chat.id,
        edit_original_msg_id=callback.message.message_id,
        edit_helper_chat_id=helper.chat.id,
        edit_helper_msg_id=helper.message_id,
        edit_old_text=old_text,
    )


@router.callback_query(F.data.startswith("task_edit_date:"))
async def callback_task_edit_date(callback: CallbackQuery, state: FSMContext):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    data = await state.get_data()
    old_due = data.get("edit_old_due", "")
    task = await task_service.get_task_by_id(task_id)
    old_due_str = _format_due_date(task) if task else old_due
    await state.update_data(edit_old_due=old_due_str)

    await state.set_state(EditTaskStates.waiting_for_due_date)
    await callback.message.edit_text(
        "📅 Введите новый срок в формате <code>ДД.ММ.ГГГГ</code> "
        "или <code>ГГГГ-ММ-ДД</code>.\n\n"
        f"Текущий: {old_due_str}\n\n"
        "Или нажмите кнопку отмены.",
        parse_mode="HTML",
        reply_markup=task_edit_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_edit_assignee:"))
async def callback_task_edit_assignee(callback: CallbackQuery, state: FSMContext):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    task = await task_service.get_task_by_id(task_id)
    old_assignees = _format_assignees(task) if task else ""
    await state.update_data(edit_old_assignees=old_assignees)

    await state.set_state(EditTaskStates.waiting_for_assignee)
    await callback.message.edit_text(
        "👤 Напишите @username исполнителя (или несколько через пробел).\n\n"
        f"Текущий: {old_assignees}\n\n"
        "Или нажмите кнопку отмены.",
        parse_mode="HTML",
        reply_markup=task_edit_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_edit_title:"))
async def callback_task_edit_title(callback: CallbackQuery, state: FSMContext):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    task = await task_service.get_task_by_id(task_id)
    old_title = task.title if task else ""
    await state.update_data(edit_old_title=old_title)

    await state.set_state(EditTaskStates.waiting_for_title)
    await callback.message.edit_text(
        f"✏️ Введите новое название задачи.\n\n"
        f"Текущее: <b>{old_title}</b>\n\n"
        "Или нажмите кнопку отмены.",
        parse_mode="HTML",
        reply_markup=task_edit_cancel_keyboard(),
    )
    await callback.answer()


def _should_apply_to_series(state_data: dict) -> bool:
    """Проверяет, редактируем ли мы всю серию."""
    return state_data.get("edit_is_series", False)


@router.message(EditTaskStates.waiting_for_title)
async def process_edit_title(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("edit_task_id")
    if not task_id:
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
        await message.answer("❌ Название не может быть пустым. Попробуйте ещё раз.")
        return

    old_title = data.get("edit_old_title", "")
    success = await task_service.update_title(task_id, new_title)
    # Если редактируем всю серию — обновляем название во всех будущих
    if _should_apply_to_series(data) and success:
        task = await task_service.get_task_by_id(task_id)
        if task and task.recurrence_group_id:
            def _update_series_titles():
                Task.objects.filter(
                    recurrence_group_id=task.recurrence_group_id,
                    status="open",
                ).exclude(id=task_id).update(title=new_title)
                # ═══ Также обновляем шаблонную задачу в TaskRecurrence ═══
                from core.models import TaskRecurrence
                try:
                    rec = TaskRecurrence.objects.get(
                        task__recurrence_group_id=task.recurrence_group_id,
                        is_active=True
                    )
                    if rec.task_id != task_id:
                        Task.objects.filter(id=rec.task_id).update(title=new_title)
                except TaskRecurrence.DoesNotExist:
                    pass
            await sync_to_async(_update_series_titles)()
            logger.info("Series title updated for group %s", task.recurrence_group_id)
    await state.clear()

    if success:
        task = await task_service.get_task_by_id(task_id)
        if task:
            new_title_str = task.title
            orig_chat = data.get("edit_original_chat_id")
            orig_msg = data.get("edit_original_msg_id")
            if orig_chat and orig_msg:
                try:
                    await message.bot.edit_message_text(
                        _build_task_text(task),
                        chat_id=orig_chat,
                        message_id=orig_msg,
                        parse_mode="HTML",
                        reply_markup=task_keyboard(task_id),
                    )
                except Exception as e:
                    logger.warning("Failed to edit task: %s", e)

            # Delete helper
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
                f"✅ Название задачи изменено:\n"
                f"{old_title} → {new_title_str}",
                parse_mode="HTML",
            )

            _notify_task_changed(task, old_title=old_title, new_title=new_title_str)
    else:
        await message.answer("❌ Не удалось обновить название.")


@router.callback_query(F.data == "task_edit_cancel")
async def callback_task_edit_cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    # Удаляем хелпер
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Восстанавливаем оригинал с кнопками
    orig_chat = data.get("edit_original_chat_id")
    orig_msg = data.get("edit_original_msg_id")
    task_id = data.get("edit_task_id")
    if orig_chat and orig_msg and task_id:
        task = await task_service.get_task_by_id(task_id)
        if task:
            try:
                await callback.bot.edit_message_text(
                    _build_task_text(task),
                    chat_id=orig_chat,
                    message_id=orig_msg,
                    parse_mode="HTML",
                    reply_markup=task_keyboard(task_id),
                )
            except Exception as e:
                logger.warning("Failed to restore task: %s", e)

    await callback.answer("Редактирование отменено.")


@router.message(EditTaskStates.waiting_for_due_date)
async def process_edit_due_date(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("edit_task_id")
    if not task_id:
        await state.clear()
        await message.answer("⚠️ Ошибка: данные потеряны. Попробуйте заново.")
        return

    text = message.text.strip()
    if text.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer("↩️ Редактирование отменено.")
        return

    new_date = None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            parsed = datetime.strptime(text, fmt)
            new_date = parsed.strftime("%Y-%m-%d")
            break
        except ValueError:
            continue

    if not new_date:
        await message.answer(
            "❌ Не удалось распознать дату. Попробуйте ещё раз:\n"
            "<code>15.07.2026</code> или <code>2026-07-15</code>\n"
            "Или напишите <b>отмена</b>.",
            parse_mode="HTML",
        )
        return

    old_due_str = data.get("edit_old_due", "")
    success = await task_service.update_due_date(task_id, new_date)
    await state.clear()

    if success:
        task = await task_service.get_task_by_id(task_id)
        if task:
            new_due_str = _format_due_date(task)
            orig_chat = data.get("edit_original_chat_id")
            orig_msg = data.get("edit_original_msg_id")
            if orig_chat and orig_msg:
                try:
                    await message.bot.edit_message_text(
                        _build_task_text(task),
                        chat_id=orig_chat,
                        message_id=orig_msg,
                        parse_mode="HTML",
                        reply_markup=task_keyboard(task_id),
                    )
                except Exception as e:
                    logger.warning("Failed to edit task: %s", e)

            # Удаляем хелпер
            helper_chat = data.get("edit_helper_chat_id")
            helper_msg = data.get("edit_helper_msg_id")
            if helper_chat and helper_msg:
                try:
                    await message.bot.delete_message(helper_chat, helper_msg)
                except Exception:
                    pass

            # Удаляем сообщение пользователя
            try:
                await message.delete()
            except Exception:
                pass

            await message.answer(
                f"✅ Срок задачи <b>{task.title}</b> изменён:\n"
                f"{old_due_str} → {new_due_str}",
                parse_mode="HTML",
            )

            # Уведомляем исполнителей об изменении
            _notify_task_changed(task, old_due_str, new_due_str, None, None)
    else:
        await message.answer("❌ Не удалось обновить срок задачи.")


@router.message(EditTaskStates.waiting_for_assignee)
async def process_edit_assignee(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("edit_task_id")
    if not task_id:
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

    old_assignees_str = data.get("edit_old_assignees", "")
    success = await task_service.update_assignees(task_id, usernames)
    # Если редактируем всю серию — обновляем исполнителей во всех будущих
    if _should_apply_to_series(data) and success:
        task = await task_service.get_task_by_id(task_id)
        if task and task.recurrence_group_id:
            def _update_series_assignees():
                from core.models import Task, TaskAssignee, TaskRecurrence
                future_tasks = Task.objects.filter(
                    recurrence_group_id=task.recurrence_group_id,
                    status="open",
                ).exclude(id=task_id)
                for ft in future_tasks:
                    ft.assignees.all().delete()
                    for ta in task.assignees.all():
                        TaskAssignee.objects.create(task=ft, user=ta.user)
                # ═══ Также обновляем шаблонную задачу в TaskRecurrence ═══
                try:
                    rec = TaskRecurrence.objects.get(
                        task__recurrence_group_id=task.recurrence_group_id,
                        is_active=True
                    )
                    if rec.task_id != task_id:
                        # Копируем assignees на шаблон
                        template_task = rec.task
                        template_task.assignees.all().delete()
                        for ta in task.assignees.all():
                            TaskAssignee.objects.create(task=template_task, user=ta.user)
                except TaskRecurrence.DoesNotExist:
                    pass
            await sync_to_async(_update_series_assignees)()
            logger.info("Series assignees updated for group %s", task.recurrence_group_id)
    await state.clear()

    if success:
        task = await task_service.get_task_by_id(task_id)
        if task:
            new_assignees_str = _format_assignees(task)
            orig_chat = data.get("edit_original_chat_id")
            orig_msg = data.get("edit_original_msg_id")
            if orig_chat and orig_msg:
                try:
                    await message.bot.edit_message_text(
                        _build_task_text(task),
                        chat_id=orig_chat,
                        message_id=orig_msg,
                        parse_mode="HTML",
                        reply_markup=task_keyboard(task_id),
                    )
                except Exception as e:
                    logger.warning("Failed to edit task: %s", e)

            # Удаляем хелпер
            helper_chat = data.get("edit_helper_chat_id")
            helper_msg = data.get("edit_helper_msg_id")
            if helper_chat and helper_msg:
                try:
                    await message.bot.delete_message(helper_chat, helper_msg)
                except Exception:
                    pass

            # Удаляем сообщение пользователя
            try:
                await message.delete()
            except Exception:
                pass

            await message.answer(
                f"✅ Исполнитель задачи <b>{task.title}</b> изменён:\n"
                f"{old_assignees_str} → {new_assignees_str}",
                parse_mode="HTML",
            )

            # Уведомляем новых исполнителей
            from celery_app.tasks.send_reminders import send_task_assigned_notification
            send_task_assigned_notification.delay(task_id)

            # Уведомляем старых исполнителей об изменении
            _notify_task_changed(task, None, None, old_assignees_str, new_assignees_str)
    else:
        await message.answer("❌ Не удалось обновить исполнителя.")


# ── Отмена серии задач ──

@router.callback_query(F.data.startswith("task_cancel_series:"))
async def callback_task_cancel_series(callback: CallbackQuery):
    """Показывает подтверждение отмены всей серии."""
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    task = await task_service.get_task_by_id(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    # Проверяем, действительно ли это часть серии
    if not task.recurrence_group_id:
        await callback.answer("Эта задача не является частью серии.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(
        f"🛑 <b>Отмена всей серии</b>\n\n"
        f"Вы уверены, что хотите отменить ВСЕ повторения задачи "
        f"<b>{task.title}</b>?\n\n"
        f"Все будущие задачи этой серии будут отменены.",
        parse_mode="HTML",
        reply_markup=task_cancel_series_confirm_keyboard(task_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_cancel_series_confirm:"))
async def callback_task_cancel_series_confirm(callback: CallbackQuery):
    """Подтверждает отмену всей серии."""
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    task = await task_service.get_task_by_id(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    success = await recurrence_service.cancel_task_series(task)
    if success:
        await callback.answer("✅ Серия задач отменена!")
        try:
            # Удаляем сообщение-подтверждение
            await callback.message.delete()
        except Exception:
            pass
        try:
            # Удаляем оригинал
            if callback.message.reply_to_message:
                await callback.message.reply_to_message.delete()
        except Exception:
            pass

        await callback.message.answer(
            f"🛑 Серия задач <b>{task.title}</b> отменена.\n\n"
            f"Новые задачи создаваться не будут. "
            f"Если понадобится — просто создайте новую задачу с таким же названием.",
            parse_mode="HTML",
        )
    else:
        await callback.answer("❌ Не удалось отменить серию.", show_alert=True)


# ── Редактирование серии задач ──

@router.callback_query(F.data.startswith("task_edit_series:"))
async def callback_task_edit_series(callback: CallbackQuery, state: FSMContext):
    """Редактирование всей серии (название, исполнители, срок)."""
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    task = await task_service.get_task_by_id(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    helper = await callback.message.answer(
        f"🔄 <b>Редактирование серии: {task.title}</b>\n\n"
        f"Изменения применятся ко ВСЕМ будущим задачам этой серии.\n\n"
        f"Что хотите изменить?",
        parse_mode="HTML",
        reply_markup=task_edit_options_keyboard(task_id, has_recurrence=True),
    )
    await callback.answer()

    await state.set_state(EditTaskStates.waiting_for_choice)
    await state.update_data(
        edit_task_id=task_id,
        edit_original_chat_id=callback.message.chat.id,
        edit_original_msg_id=callback.message.message_id,
        edit_helper_chat_id=helper.chat.id,
        edit_helper_msg_id=helper.message_id,
        edit_is_series=True,
    )


@router.callback_query(F.data.startswith("task_cycle_priority:"))
async def callback_task_cycle_priority(callback: CallbackQuery):
    """Циклически меняет приоритет задачи."""
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID.", show_alert=True)
        return

    priority_cycle = ['normal', 'high', 'critical', 'low']
    task = await task_service.get_task_by_id(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    current = getattr(task, 'priority', 'normal') or 'normal'
    try:
        idx = priority_cycle.index(current)
    except ValueError:
        idx = 0
    new_priority = priority_cycle[(idx + 1) % len(priority_cycle)]

    from core.models import Task as TaskModel
    await sync_to_async(TaskModel.objects.filter(id=task_id).update)(priority=new_priority)

    task = await task_service.get_task_by_id(task_id)
    # ═══ Редактируем helper на новый helper (обновляем только кнопки), а оригинал не трогаем ═══
    # Просто отвечаем callback'ом — пользователь увидит новое сообщение при следующем /tasks
    await callback.answer(f"🔽 Приоритет изменён: {new_priority}")


def _notify_task_changed(task, old_due=None, new_due=None, old_assign=None, new_assign=None, old_title=None, new_title=None):
    """Отправляет уведомление исполнителям об изменении задачи через Celery."""
    changes = []
    if old_title and new_title and old_title != new_title:
        changes.append(f"✏️ Название: {old_title} → {new_title}")
    if old_due and new_due and old_due != new_due:
        changes.append(f"📅 Срок: {old_due} → {new_due}")
    if old_assign and new_assign and old_assign != new_assign:
        changes.append(f"👤 Исполнитель: {old_assign} → {new_assign}")
    if not changes:
        return
    from celery_app.tasks.send_reminders import send_task_changed_notification
    send_task_changed_notification.delay(task.id, "\n".join(changes))
