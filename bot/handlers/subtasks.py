"""
Хендлеры подзадач (SubTask).

UX — как редактирование задачи (без FSM, через PENDING-словарь):
  ➕ Подзадача        -> всплывает helper с кнопкой «↩️ Отмена», пользователь
                         пишет текст -> подзадача создаётся, helper и ввод
                         удаляются, исходная карточка задачи обновляется.
  🗂 Подзадачи        -> меню управления подзадачами (прогресс + кнопки).
В меню на каждую подзадачу по строке из двух кнопок:
  [иконка+название+@отв.] -> переключить статус (pending->in_progress->done->pending)
  [👤 @отв.]           -> сменить исполнителя (ввод @username, helper + отмена)
Переключать статус / менять исполнителя может только:
  admin, либо manager-создатель задачи, либо ответственный за эту подзадачу.
"""
import logging
import re
import time
from datetime import timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from django.utils import timezone

from core.models import Task, TelegramUser, SubTask, UserRole
from core.services.subtask_service import (
    add_subtask,
    set_subtask_status,
    assign_subtask_users,
    build_subtasks_section,
)
from bot.handlers.tasks import _build_task_text
from bot.keyboards.inline import task_keyboard

logger = logging.getLogger(__name__)
router = Router()

# ── PENDING (вместо FSM, чтобы FSMTimeoutMiddleware не сбрасывал состояние) ──
PENDING = {}
_TTL = 300


def _set_pending(user_id, data):
    data["expires"] = time.time() + _TTL
    PENDING[user_id] = data


def _peek_pending(user_id):
    d = PENDING.get(user_id)
    if not d:
        return None
    if d.get("expires", 0) < time.time():
        PENDING.pop(user_id, None)
        return None
    return d


def _clear_pending(user_id):
    PENDING.pop(user_id, None)


# ── helpers ──

def _reload_task(task_id: int) -> Task:
    return (
        Task.objects.filter(id=task_id)
        .select_related("creator", "topic__chat")
        .prefetch_related("assignees__user", "subtasks__assignees")
        .first()
    )


async def _reload_task_async(task_id: int) -> Task:
    return await sync_to_async(_reload_task)(task_id)


def _card_text(task: Task) -> str:
    return _build_task_text(task)


def _parse_subtask_input(text: str) -> dict:
    """Извлекает @username и срок `до ДД.ММ` из ввода подзадачи."""
    users = re.findall(r"@(\w+)", text)
    due = None
    m = re.search(r"до\s+(\d{1,2})[.\/](\d{1,2})", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = timezone.localtime(timezone.now()).year
        try:
            due = f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            due = None
    title = text
    for u in users:
        title = title.replace(f"@{u}", "")
    title = re.sub(r"до\s+\d{1,2}[.\/]\d{1,2}", "", title)
    title = re.sub(r"\s+", " ", title).strip(" \t-–—,:")
    return {"title": title, "users": users, "due": due}


def _can_toggle(user, task, sub) -> bool:
    """admin ИЛИ manager-создатель задачи ИЛИ ответственный за подзадачу."""
    if not user:
        return False
    chat = task.topic.chat if (task.topic and task.topic.chat) else None
    role = None
    if chat:
        role = UserRole.objects.filter(user=user, chat=chat).values_list("role", flat=True).first()
    if role == "admin":
        return True
    if role == "manager" and task.creator_id == user.id:
        return True
    if sub.assignees.filter(id=user.id).exists():
        return True
    return False


def _resp_label(sub) -> str:
    users = list(sub.assignees.all())
    if not users:
        return ""
    return " " + " ".join(
        f"@{u.username}" if u.username else (u.full_name or f"id={u.id}") for u in users
    )


# ── клавиатуры (локально, чтобы не зависеть от inline.py) ──

def _subtasks_menu_keyboard(task_id: int, subs):
    from core.services.subtask_service import STATUS_ICONS
    builder = InlineKeyboardBuilder()
    for s in subs:
        icon = STATUS_ICONS.get(s.status, "⬜️")
        builder.add(InlineKeyboardButton(
            text=f"{icon} {s.title}{_resp_label(s)}",
            callback_data=f"task_sub_toggle:{task_id}:{s.id}",
        ))
        builder.add(InlineKeyboardButton(
            text=f"👤{_resp_label(s)}",
            callback_data=f"task_sub_assign:{task_id}:{s.id}",
        ))
    builder.add(InlineKeyboardButton(
        text="➕ Подзадача",
        callback_data=f"task_add_subtask:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Назад",
        callback_data=f"task_subs_close:{task_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def _subtask_add_cancel_keyboard(task_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Отмена",
        callback_data=f"task_sub_add_cancel:{task_id}",
    ))
    return builder.as_markup()


def _subtask_assign_cancel_keyboard(task_id: int, sub_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Отмена",
        callback_data=f"task_sub_assign_cancel:{task_id}:{sub_id}",
    ))
    return builder.as_markup()


# ── ➕ Подзадача: открыть ввод ──

@router.callback_query(F.data.startswith("task_add_subtask:"))
async def cb_task_add_subtask(callback: CallbackQuery):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID.", show_alert=True)
        return
    task = await _reload_task_async(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    helper = await callback.message.answer(
        f"➕ <b>Новая подзадача</b>\n\n"
        f"Введите подзадачу для «{task.title}».\n"
        f"Формат: <i>название @username до ДД.ММ</i> (исполнитель и срок — опционально).\n"
        f"Например: <i>написать код @JDexMoment до 10.08</i>\n\n"
        f"Или нажмите «↩️ Отмена».",
        parse_mode="HTML",
        reply_markup=_subtask_add_cancel_keyboard(task_id),
    )
    _set_pending(callback.from_user.id, {
        "kind": "subtask_add",
        "task_id": task_id,
        "orig_chat": callback.message.chat.id,
        "orig_msg": callback.message.message_id,
        "helper_chat": helper.chat.id,
        "helper_msg": helper.message_id,
    })
    await callback.answer()


@router.callback_query(F.data.startswith("task_sub_add_cancel:"))
async def cb_task_sub_add_cancel(callback: CallbackQuery):
    _clear_pending(callback.from_user.id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("↩️ Отменено")


# ── 👤 сменить исполнителя подзадачи: открыть ввод ──

@router.callback_query(F.data.startswith("task_sub_assign:"))
async def cb_task_sub_assign(callback: CallbackQuery):
    parts = callback.data.split(":")
    try:
        task_id = int(parts[1])
        sub_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID.", show_alert=True)
        return

    sub = await sync_to_async(
        lambda: SubTask.objects.filter(id=sub_id, parent_task_id=task_id)
        .prefetch_related("assignees").first()
    )()
    if not sub:
        await callback.answer("Подзадача не найдена.", show_alert=True)
        return

    task = await _reload_task_async(task_id)
    db_user = await sync_to_async(
        lambda: TelegramUser.objects.filter(telegram_id=callback.from_user.id).first()
    )()
    if not _can_toggle(db_user, task, sub):
        await callback.answer("❌ Нет прав менять исполнителя.", show_alert=True)
        return

    helper = await callback.message.answer(
        f"👤 <b>Назначить исполнителя</b>\n\n"
        f"Подзадача: {sub.title}{_resp_label(sub)}\n"
        f"Напишите @username (или несколько через пробел).\n"
        f"Или нажмите «↩️ Отмена».",
        parse_mode="HTML",
        reply_markup=_subtask_assign_cancel_keyboard(task_id, sub_id),
    )
    _set_pending(callback.from_user.id, {
        "kind": "subtask_assign",
        "task_id": task_id,
        "sub_id": sub_id,
        "orig_chat": callback.message.chat.id,
        "orig_msg": callback.message.message_id,
        "helper_chat": helper.chat.id,
        "helper_msg": helper.message_id,
    })
    await callback.answer()


@router.callback_query(F.data.startswith("task_sub_assign_cancel:"))
async def cb_task_sub_assign_cancel(callback: CallbackQuery):
    _clear_pending(callback.from_user.id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("↩️ Отменено")


# ── перехват введённого текста (добавление / смена исполнителя) ──

@router.message(F.text & ~F.text.startswith("/"))
async def capture_subtask_input(message: Message):
    data = _peek_pending(message.from_user.id)
    if not data:
        return
    text = (message.text or "").strip()

    # отмена текстом
    if text.lower() in ("отмена", "cancel", "/cancel"):
        _clear_pending(message.from_user.id)
        try:
            await message.bot.delete_message(data["helper_chat"], data["helper_msg"])
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer("↩️ Отменено.")
        return

    if data["kind"] == "subtask_add":
        parsed = _parse_subtask_input(text)
        if not parsed["title"]:
            await message.answer("❌ Название подзадачи не может быть пустым.")
            return
        sub = await add_subtask(
            data["task_id"], parsed["title"],
            due_date_str=parsed["due"],
            assignee_usernames=parsed["users"],
        )
        if not sub:
            await message.answer("❌ Не удалось добавить подзадачу.")
            return
        status_msg = "✅ Подзадача добавлена."

    elif data["kind"] == "subtask_assign":
        usernames = re.findall(r"@\w+", text)
        if not usernames:
            await message.answer("❌ Не найден @username. Напишите в формате @user.")
            return
        ok = await assign_subtask_users(data["task_id"], data["sub_id"], usernames)
        if not ok:
            await message.answer("❌ Не удалось назначить исполнителя.")
            return
        status_msg = "✅ Исполнитель(и) назначен(ы)."
    else:
        _clear_pending(message.from_user.id)
        return

    _clear_pending(message.from_user.id)

    # удаляем helper и ввод пользователя
    try:
        await message.bot.delete_message(data["helper_chat"], data["helper_msg"])
    except Exception:
        pass
    try:
        await message.delete()
    except Exception:
        pass

    # обновляем исходную карточку/меню
    task = await _reload_task_async(data["task_id"])
    if task:
        try:
            await message.bot.edit_message_text(
                _card_text(task),
                chat_id=data["orig_chat"],
                message_id=data["orig_msg"],
                parse_mode="HTML",
                reply_markup=task_keyboard(task.id, has_subtasks=bool(task.subtasks.all())),
            )
        except Exception as e:
            logger.warning("Failed to update card after subtask action: %s", e)
    await message.answer(status_msg)


# ── переключить статус подзадачи (меню управления) ──

@router.callback_query(F.data.startswith("task_sub_toggle:"))
async def cb_task_sub_toggle(callback: CallbackQuery):
    parts = callback.data.split(":")
    try:
        task_id = int(parts[1])
        sub_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID.", show_alert=True)
        return

    sub = await sync_to_async(
        lambda: SubTask.objects.filter(id=sub_id, parent_task_id=task_id)
        .prefetch_related("assignees").first()
    )()
    if not sub:
        await callback.answer("Подзадача не найдена.", show_alert=True)
        return

    task = await _reload_task_async(task_id)
    db_user = await sync_to_async(
        lambda: TelegramUser.objects.filter(telegram_id=callback.from_user.id).first()
    )()
    if not _can_toggle(db_user, task, sub):
        await callback.answer("❌ Нет прав менять статус подзадачи.", show_alert=True)
        return

    cycle = {"pending": "in_progress", "in_progress": "done", "done": "pending"}
    new_status = cycle.get(sub.status, "done")
    all_done = await set_subtask_status(task_id, sub_id, new_status)

    task = await _reload_task_async(task_id)
    subs = list(task.subtasks.all())
    await callback.message.edit_text(
        build_subtasks_section(task),
        parse_mode="HTML",
        reply_markup=_subtasks_menu_keyboard(task_id, subs),
    )
    await callback.answer(
        {"done": "✅ Готово", "in_progress": "🔄 В работе", "pending": "⬜️ Ожидает"}.get(new_status, "")
    )
    if all_done:
        await callback.message.answer("🎉 Все подзадачи выполнены — задача закрыта.")


# ── открыть меню управления подзадачами ──

@router.callback_query(F.data.startswith("task_subs:"))
async def cb_task_subs(callback: CallbackQuery):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID.", show_alert=True)
        return
    task = await _reload_task_async(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    subs = list(task.subtasks.all())
    await callback.message.edit_text(
        build_subtasks_section(task),
        parse_mode="HTML",
        reply_markup=_subtasks_menu_keyboard(task_id, subs),
    )
    await callback.answer()


# ── закрыть меню управления (вернуться к карточке задачи) ──

@router.callback_query(F.data.startswith("task_subs_close:"))
async def cb_task_subs_close(callback: CallbackQuery):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID.", show_alert=True)
        return
    task = await _reload_task_async(task_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return
    await callback.message.edit_text(
        _card_text(task),
        parse_mode="HTML",
        reply_markup=task_keyboard(task.id, has_subtasks=bool(task.subtasks.all())),
    )
    await callback.answer()
