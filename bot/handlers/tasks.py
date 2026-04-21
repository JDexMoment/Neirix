import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from django.utils import timezone
from core.models import Task
from core.services.task_service import TaskService
from bot.utils import get_chat_context

logger = logging.getLogger(__name__)
router = Router()
task_service = TaskService()


def get_task_keyboard(task_id: int):
    """Создаёт inline-клавиатуру с кнопкой 'Выполнено'."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Выполнено", callback_data=f"task_done:{task_id}")
    return builder.as_markup()


@router.message(Command("task"))
async def cmd_task(message: Message):
    """Обработчик команд /task list и /task done <номер>."""
    chat, topic, db_user = await get_chat_context(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer(
            "Используйте:\n"
            "/task list — список задач\n"
            "/task done <номер> — отметить задачу выполненной"
        )
        return

    subcommand = args[1].lower()

    if subcommand == "list":
        # Выбираем задачи в зависимости от типа чата
        if message.chat.type == "private":
            # ЛС: только задачи, назначенные пользователю
            get_tasks = sync_to_async(lambda: list(
                Task.objects.filter(
                    assignee=db_user,
                    status='open'
                ).select_related('topic__chat', 'assignee').order_by('due_date')
            ))
            tasks = await get_tasks()
            header = "📋 Ваши задачи:\n"
        else:
            # Группа: все открытые задачи чата (и возможно темы)
            filters = {
                'topic__chat': chat,
                'status': 'open'
            }
            if topic:
                filters['topic'] = topic
            get_tasks = sync_to_async(lambda: list(
                Task.objects.filter(**filters)
                .select_related('topic__chat', 'assignee')
                .order_by('due_date')
            ))
            tasks = await get_tasks()
            header = f"📋 Задачи чата {chat.title}:\n"

        if not tasks:
            await message.answer("Нет открытых задач.")
            return

        lines = [header]
        for i, task in enumerate(tasks, 1):
            # Форматируем ответственного
            assignee_str = f"@{task.assignee.username}" if task.assignee and task.assignee.username else (
                task.assignee.full_name if task.assignee else "не назначен"
            )
            due_str = f"📅 до {timezone.localtime(task.due_date).strftime('%d.%m.%Y')}" if task.due_date else "без срока"
            lines.append(
                f"{i}. <b>{task.title}</b>\n"
                f"   👤 {assignee_str}\n"
                f"   {due_str}\n"
            )
            # Отправляем каждую задачу отдельным сообщением с кнопкой
            await message.answer(
                f"{i}. <b>{task.title}</b>\n"
                f"👤 {assignee_str}\n"
                f"{due_str}",
                parse_mode="HTML",
                reply_markup=get_task_keyboard(task.id)
            )
        # Если задач много, лучше отправлять по одной, как выше.
        # Можно и одним сообщением, но тогда кнопка будет одна на все.

    elif subcommand == "done":
        if len(args) < 3:
            await message.answer("Укажите номер задачи: /task done 1")
            return
        try:
            task_num = int(args[2]) - 1
            # Получаем тот же список, что и в list
            if message.chat.type == "private":
                get_tasks = sync_to_async(lambda: list(
                    Task.objects.filter(
                        assignee=db_user,
                        status='open'
                    ).select_related('topic__chat', 'assignee').order_by('due_date')
                ))
            else:
                filters = {'topic__chat': chat, 'status': 'open'}
                if topic:
                    filters['topic'] = topic
                get_tasks = sync_to_async(lambda: list(
                    Task.objects.filter(**filters)
                    .select_related('topic__chat', 'assignee')
                    .order_by('due_date')
                ))
            tasks = await get_tasks()

            if 0 <= task_num < len(tasks):
                task = tasks[task_num]
                success = await sync_to_async(task_service.mark_task_done)(task.id, db_user)
                if success:
                    await message.answer(f"✅ Задача '{task.title}' отмечена выполненной.")
                else:
                    await message.answer("❌ Не удалось отметить задачу.")
            else:
                await message.answer("Неверный номер задачи.")
        except ValueError:
            await message.answer("Номер задачи должен быть числом.")
    else:
        await message.answer("Неизвестная подкоманда. Используйте list или done.")


@router.callback_query(F.data.startswith("task_done:"))
async def callback_task_done(callback: CallbackQuery):
    """Обработчик нажатия на кнопку 'Выполнено'."""
    # Получаем пользователя из callback
    from core.models import TelegramUser
    db_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=callback.from_user.id)

    task_id = int(callback.data.split(":")[1])
    success = await sync_to_async(task_service.mark_task_done)(task_id, db_user)

    if success:
        await callback.answer("✅ Задача выполнена!")
        # Убираем кнопку из сообщения
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("✅ Задача отмечена как выполненная.")
    else:
        await callback.answer("❌ Ошибка: задача не найдена или нет прав.")