from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from core.models import Task
from core.services.task_service import TaskService
from bot.utils import get_chat_context

router = Router()
task_service = TaskService()


def get_task_keyboard(task_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Выполнено", callback_data=f"task_done:{task_id}")
    return builder.as_markup()


@router.message(Command("task"))
async def cmd_task(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not chat:
        return  # пользователь получил сообщение с инструкцией

    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer(
            "Используйте:\n"
            "/task list — список ваших задач\n"
            "/task done <номер> — отметить задачу выполненной"
        )
        return

    subcommand = args[1].lower()
    if subcommand == "list":
        # Получаем задачи пользователя в рамках привязанного чата (или текущего)
        tasks = await sync_to_async(task_service.get_user_tasks)(db_user, status='open')
        # Дополнительно фильтруем по чату/теме, если находимся в группе
        if chat:
            tasks = [t for t in tasks if t.topic.chat_id == chat.id]
            if topic:
                tasks = [t for t in tasks if t.topic_id == topic.id]
        if not tasks:
            await message.answer("У вас нет открытых задач в этом чате. 🎉")
            return
        for i, task in enumerate(tasks, 1):
            due = f"до {task.due_date.strftime('%d.%m.%Y')}" if task.due_date else "без срока"
            text = f"{i}. <b>{task.title}</b>\n{task.description}\n📅 {due}"
            await message.answer(text, parse_mode="HTML", reply_markup=get_task_keyboard(task.id))
    elif subcommand == "done":
        if len(args) < 3:
            await message.answer("Укажите номер задачи: /task done 1")
            return
        try:
            task_num = int(args[2]) - 1
            # Получаем список задач аналогично list
            tasks = await sync_to_async(task_service.get_user_tasks)(db_user, status='open')
            if chat:
                tasks = [t for t in tasks if t.topic.chat_id == chat.id]
                if topic:
                    tasks = [t for t in tasks if t.topic_id == topic.id]
            if 0 <= task_num < len(tasks):
                task = tasks[task_num]
                success = await sync_to_async(task_service.mark_task_done)(task.id, db_user)
                if success:
                    await message.answer(f"Задача '{task.title}' отмечена выполненной. ✅")
                else:
                    await message.answer("Не удалось отметить задачу.")
            else:
                await message.answer("Неверный номер задачи.")
        except ValueError:
            await message.answer("Номер задачи должен быть числом.")


@router.callback_query(F.data.startswith("task_done:"))
async def callback_task_done(callback: CallbackQuery):
    # Получаем пользователя и чат из callback (можно упростить, но нужно проверить права)
    from core.models import TelegramUser
    db_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=callback.from_user.id)
    task_id = int(callback.data.split(":")[1])
    success = await sync_to_async(task_service.mark_task_done)(task_id, db_user)
    if success:
        await callback.answer("Задача выполнена!")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("✅ Задача отмечена как выполненная.")
    else:
        await callback.answer("Ошибка: задача не найдена или нет прав.")