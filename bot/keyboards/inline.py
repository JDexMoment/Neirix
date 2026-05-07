from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton


# ─────────────────────────────────────────────────────────────────────
# Задачи
# ─────────────────────────────────────────────────────────────────────


def task_keyboard(task_id: int):
    """Кнопка 'Выполнено' под каждой задачей."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✅ Выполнено",
        callback_data=f"task_done:{task_id}",
    ))
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────────────
# Встречи
# ─────────────────────────────────────────────────────────────────────


def meeting_keyboard(meeting_id: int):
    """Кнопки 'Перенести' и 'Отменить' под каждой встречей."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="📅 Перенести",
        callback_data=f"meeting_reschedule:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="❌ Отменить",
        callback_data=f"meeting_cancel:{meeting_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def meeting_cancel_confirm_keyboard(meeting_id: int):
    """Подтверждение отмены встречи."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✅ Да, отменить",
        callback_data=f"meeting_cancel_confirm:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Нет, оставить",
        callback_data=f"meeting_cancel_abort:{meeting_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def meeting_reschedule_cancel_keyboard():
    """Кнопка отмены переноса встречи."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Отмена",
        callback_data="meeting_reschedule_cancel",
    ))
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────────────
# Общие
# ─────────────────────────────────────────────────────────────────────


def confirm_keyboard(action: str, item_id: int):
    """Универсальная клавиатура подтверждения."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✅ Да",
        callback_data=f"confirm:{action}:{item_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="❌ Нет",
        callback_data="cancel",
    ))
    builder.adjust(2)
    return builder.as_markup()