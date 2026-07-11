import logging
import re
from datetime import datetime
from typing import List, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from asgiref.sync import sync_to_async
from django.utils import timezone

from core.models import Task, TelegramUser, TelegramChat, UserRole
from core.services.task_service import TaskService, _find_user_by_username, _is_bot_user
from bot.utils import get_chat_context
from bot.keyboards.inline import (
    task_keyboard,
    task_edit_options_keyboard,
    task_edit_cancel_keyboard,
    task_assign_keyboard,
)
from bot.states import EditTaskStates, AssignTaskStates

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


def _get_open_tasks_for_chat(chat, topic=None) -> List[Task]:
    filters = {
        "topic__chat": chat,
        "status": "open",
    }
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

    now = timezone.localtime(timezone.now())

    if task.status == "open" and task.due_date < timezone.now():
        # Просрочено — показываем на сколько
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
    """Форматирует создателя задачи/встречи."""
    if not creator:
        return "неизвестен"
    if creator.username:
        return f"@{creator.username}"
    return creator.full_name or f"id={creator.id}"


def _format_task_source(task: Task) -> str:
    """Возвращает название чата, откуда задача."""
    try:
        chat_title = task.topic.chat.title
        if chat_title:
            return f"📍 Чат: {chat_title}"
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────
# /tasks — список задач
# ─────────────────────────────────────────────────────────────────────


@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    if message.chat.type == "private":
        tasks = await sync_to_async(_get_open_tasks_for_private)(db_user)
        header = "📋 Ваши задачи:"
    else:
        tasks = await sync_to_async(_get_open_tasks_for_chat)(chat, topic)
        if not chat:
            await message.answer("Не удалось определить чат.")
            return
        header = f"📋 Задачи чата {chat.title}:"

    if not tasks:
        await message.answer("Нет открытых задач.")
        return

    await message.answer(header)

    for i, task in enumerate(tasks, 1):
        assignee_str = _format_assignees(task)
        due_str = _format_due_date(task)
        creator_str = _format_creator(task.creator)

        await message.answer(
            f"{i}. <b>{task.title}</b>\n"
            f"👤 {assignee_str}\n"
            f"{due_str}\n"
            f"📝 Назначил(а): {creator_str}",
            parse_mode="HTML",
            reply_markup=task_keyboard(task.id),
        )


# ─────────────────────────────────────────────────────────────────────
# callback: task_done
# ─────────────────────────────────────────────────────────────────────


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
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception as e:
            logger.warning("Failed to remove task inline keyboard: %s", e)
        await callback.message.reply("✅ Задача отмечена как выполненная.")
    else:
        await callback.answer("❌ Ошибка: задача не найдена или нет прав.", show_alert=True)


# ─────────────────────────────────────────────────────────────────────
# Редактирование задачи — шаг 1: меню выбора
# ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("task_edit:"))
async def callback_task_edit(callback: CallbackQuery):
    """Показывает меню выбора: изменить срок или исполнителя."""
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

    await callback.message.edit_text(
        f"✏️ <b>{task.title}</b>\n"
        f"👤 {assignee_str}\n"
        f"{due_str}\n\n"
        f"Что вы хотите изменить?",
        parse_mode="HTML",
        reply_markup=task_edit_options_keyboard(task_id),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────────────
# Редактирование — назад к задаче
# ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("task_back:"))
async def callback_task_back(callback: CallbackQuery):
    """Возвращает к просмотру задачи."""
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
    creator_str = _format_creator(task.creator)

    await callback.message.edit_text(
        f"<b>{task.title}</b>\n"
        f"👤 {assignee_str}\n"
        f"{due_str}\n"
        f"📝 Назначил(а): {creator_str}",
        parse_mode="HTML",
        reply_markup=task_keyboard(task_id),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────────────
# Редактирование — изменение срока
# ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("task_edit_date:"))
async def callback_task_edit_date(callback: CallbackQuery, state: FSMContext):
    """Запрашивает новый срок."""
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    await state.set_state(EditTaskStates.waiting_for_due_date)
    await state.update_data(edit_task_id=task_id)

    await callback.message.edit_text(
        "📅 Введите новый срок в формате <code>ДД.ММ.ГГГГ</code> "
        "или <code>ГГГГ-ММ-ДД</code>.\n\n"
        "Например: <code>15.07.2026</code>\n\n"
        "Или нажмите кнопку отмены.",
        parse_mode="HTML",
        reply_markup=task_edit_cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditTaskStates.waiting_for_due_date)
async def process_edit_due_date(message: Message, state: FSMContext):
    """Принимает новую дату для задачи."""
    data = await state.get_data()
    task_id = data.get("edit_task_id")
    if not task_id:
        await state.clear()
        await message.answer("⚠️ Ошибка: данные потеряны. Попробуйте заново.")
        return

    text = message.text.strip()

    # Проверяем отмену
    if text.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await message.answer("↩️ Редактирование отменено.")
        return

    # Парсим дату
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

    success = await task_service.update_due_date(task_id, new_date)
    await state.clear()

    if success:
        task = await task_service.get_task_by_id(task_id)
        if task:
            await message.answer(
                f"✅ Срок задачи <b>{task.title}</b> обновлён:\n"
                f"{_format_due_date(task)}",
                parse_mode="HTML",
            )
        else:
            await message.answer("✅ Срок задачи обновлён.")
    else:
        await message.answer("❌ Не удалось обновить срок задачи.", parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────
# Редактирование — изменение исполнителя
# ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("task_edit_assignee:"))
async def callback_task_edit_assignee(callback: CallbackQuery, state: FSMContext):
    """Запрашивает нового исполнителя."""
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    await state.set_state(EditTaskStates.waiting_for_assignee)
    await state.update_data(edit_task_id=task_id)

    await callback.message.edit_text(
        "👤 Напишите @username исполнителя (или несколько через пробел).\n\n"
        "Например: <code>@ivanov</code> или <code>@ivanov @petrov</code>\n\n"
        "Или нажмите кнопку отмены.",
        parse_mode="HTML",
        reply_markup=task_edit_cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditTaskStates.waiting_for_assignee)
async def process_edit_assignee(message: Message, state: FSMContext):
    """Принимает нового исполнителя для задачи."""
    data = await state.get_data()
    task_id = data.get("edit_task_id")
    if not task_id:
        await state.clear()
        await message.answer("⚠️ Ошибка: данные потеряны. Попробуйте заново.")
        return

    text = message.text.strip()

    if text.lower() in ("отмена", "cancel", "/cancel", "-"):
        await state.clear()
        await message.answer("↩️ Редактирование отменено.")
        return

    # Извлекаем @username из текста
    usernames = re.findall(r"@\w+", text)
    if not usernames:
        await message.answer(
            "❌ Не найден @username. Напишите в формате <code>@username</code>.\n"
            "Или напишите <b>отмена</b>.",
            parse_mode="HTML",
        )
        return

    success = await task_service.update_assignees(task_id, usernames)
    await state.clear()

    if success:
        task = await task_service.get_task_by_id(task_id)
        if task:
            assignee_str = _format_assignees(task)
            await message.answer(
                f"✅ Исполнитель задачи <b>{task.title}</b> обновлён:\n"
                f"👤 {assignee_str}",
                parse_mode="HTML",
            )

            # Уведомляем новых исполнителей в ЛС
            await _notify_new_assignees(task)
        else:
            await message.answer("✅ Исполнитель задачи обновлён.")
    else:
        await message.answer("❌ Не удалось обновить исполнителя.", parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────
# Уведомление новых исполнителей
# ─────────────────────────────────────────────────────────────────────


async def _notify_new_assignees(task, bot=None):
    """Отправляет уведомление исполнителям через Celery."""
    from celery_app.tasks.send_reminders import send_task_assigned_notification
    send_task_assigned_notification.delay(task.id)


# ─────────────────────────────────────────────────────────────────────
# callback: отмена редактирования
# ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "task_edit_cancel")
async def callback_task_edit_cancel(callback: CallbackQuery, state: FSMContext):
    """Отменяет редактирование задачи."""
    await state.clear()
    await callback.answer("Редактирование отменено.")
    try:
        await callback.message.edit_text("↩️ Редактирование отменено.")
    except Exception:
        await callback.message.reply("↩️ Редактирование отменено.")


# ─────────────────────────────────────────────────────────────────────
# Назначение исполнителя для задачи без assignee
# (через уведомление создателю в ЛС)
# ─────────────────────────────────────────────────────────────────────


async def notify_creator_about_unassigned_task(task: Task, bot=None):
    """Уведомляет создателя о задаче без исполнителя через Celery."""
    from celery_app.tasks.send_reminders import send_unassigned_task_notification
    send_unassigned_task_notification.delay(task.id)


@router.message(AssignTaskStates.waiting_for_assignee_username)
async def process_assign_task(message: Message, state: FSMContext):
    """Принимает @username для назначения на задачу без исполнителя."""
    data = await state.get_data()
    task_id = data.get("assign_task_id")
    if not task_id:
        await state.clear()
        await message.answer("⚠️ Ошибка: данные потеряны. Попробуйте заново.")
        return

    text = message.text.strip()

    if text.lower() in ("отмена", "cancel", "/cancel", "-"):
        await state.clear()
        await message.answer("↩️ Назначение отменено.")
        return

    usernames = re.findall(r"@\w+", text)
    if not usernames:
        await message.answer(
            "❌ Не найден @username. Напишите в формате <code>@username</code>.\n"
            "Или напишите <b>отмена</b>.",
            parse_mode="HTML",
        )
        return

    success = await task_service.update_assignees(task_id, usernames)
    await state.clear()

    if success:
        task = await task_service.get_task_by_id(task_id)
        if task:
            assignee_str = _format_assignees(task)
            await message.answer(
                f"✅ Исполнитель назначен!\n"
                f"<b>{task.title}</b>\n"
                f"👤 {assignee_str}",
                parse_mode="HTML",
            )
            # Уведомляем новых исполнителей
            await _notify_new_assignees(task)
        else:
            await message.answer("✅ Исполнитель назначен.")
    else:
        await message.answer("❌ Не удалось назначить исполнителя.")


@router.message(F.text.regexp(r"^@\w+"), AssignTaskStates.waiting_for_assignee_username)
async def process_assign_task_simple(message: Message, state: FSMContext):
    """Альтернативный хендлер для @username через AssignTaskStates."""
    await process_assign_task(message, state)
