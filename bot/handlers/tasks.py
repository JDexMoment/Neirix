import logging
from typing import List, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from asgiref.sync import sync_to_async
from django.utils import timezone

from core.models import Task, TelegramUser
from core.services.task_service import TaskService
from bot.utils import get_chat_context
from bot.keyboards.inline import task_keyboard

logger = logging.getLogger(__name__)
router = Router()
task_service = TaskService()


def _get_open_tasks_for_private(db_user: TelegramUser) -> List[Task]:
    """Задачи, где пользователь — исполнитель (для лички)."""
    return list(
        Task.objects.filter(
            assignees__user=db_user,
            status="open",
        )
        .select_related("topic__chat", "source_message__author")
        .prefetch_related("assignees__user")
        .order_by("due_date", "id")
        .distinct()
    )


def _get_open_tasks_for_chat(chat) -> List[Task]:
    """ВСЕ открытые задачи чата (без фильтра по топику)."""
    return list(
        Task.objects.filter(
            topic__chat=chat,
            status="open",
        )
        .select_related("topic__chat", "source_message__author")
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
    return f"📅 до {dt.strftime('%d.%m.%Y')}"


def _format_assignees(task: Task) -> str:
    assignee_list = [a.user for a in task.assignees.all()]
    if not assignee_list:
        return "не назначен"
    return ", ".join(
        f"@{u.username}" if u.username else (u.full_name or f"id={u.telegram_id}")
        for u in assignee_list
    )


def _format_assigner(task: Task) -> str:
    """Кто создал/назначил задачу."""
    try:
        if task.source_message and task.source_message.author:
            author = task.source_message.author
            if author.username:
                return f"@{author.username}"
            return author.full_name or str(author.telegram_id)
    except Exception:
        pass
    return "—"


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
        if not chat:
            await message.answer("Не удалось определить чат.")
            return
        # В группе показываем ВСЕ задачи чата (без фильтра по топику)
        tasks = await sync_to_async(_get_open_tasks_for_chat)(chat)
        header = f"📋 Задачи чата {chat.title}:"

    if not tasks:
        await message.answer("Нет открытых задач.")
        return

    await message.answer(header)

    for i, task in enumerate(tasks, 1):
        assignee_str = _format_assignees(task)
        due_str = _format_due_date(task)
        assigner_str = _format_assigner(task)

        await message.answer(
            f"{i}. <b>{task.title}</b>\n"
            f"👤 {assignee_str}\n"
            f"{due_str}\n"
            f"📝 Назначил(а): {assigner_str}",
            parse_mode="HTML",
            reply_markup=task_keyboard(task.id),
        )


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
