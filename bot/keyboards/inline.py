from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton


# ─────────────────────────────────────────────────────────────────────
# Задачи
# ─────────────────────────────────────────────────────────────────────


def task_keyboard(task_id: int):
    """Кнопки 'Выполнено' и 'Редактировать' под каждой задачей."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✅ Выполнено",
        callback_data=f"task_done:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="✏️ Редактировать",
        callback_data=f"task_edit:{task_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def task_edit_options_keyboard(task_id: int):
    """Выбор: изменить срок, исполнителя или название."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="📅 Срок",
        callback_data=f"task_edit_date:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="👤 Исполнитель",
        callback_data=f"task_edit_assignee:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="✏️ Название",
        callback_data=f"task_edit_title:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Назад к задаче",
        callback_data=f"task_back:{task_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def task_edit_cancel_keyboard():
    """Кнопка отмены редактирования."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Отмена",
        callback_data="task_edit_cancel",
    ))
    return builder.as_markup()


def task_assign_keyboard(task_id: int):
    """Кнопка 'Назначить исполнителя' для задач без assignee."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="👤 Назначить исполнителя",
        callback_data=f"task_edit_assignee:{task_id}",
    ))
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────────────
# Встречи
# ─────────────────────────────────────────────────────────────────────


def meeting_keyboard(meeting_id: int):
    """Кнопки 'Редактировать' и 'Отменить' под каждой встречей."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✏️ Редактировать",
        callback_data=f"meeting_edit:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="❌ Отменить",
        callback_data=f"meeting_cancel:{meeting_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def meeting_edit_options_keyboard(meeting_id: int):
    """Выбор: изменить дату, участников или название."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="📅 Перенести",
        callback_data=f"meeting_reschedule:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="👤 Участники",
        callback_data=f"meeting_edit_participants:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="✏️ Название",
        callback_data=f"meeting_edit_title:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Назад к встрече",
        callback_data=f"meeting_back:{meeting_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def meeting_edit_cancel_keyboard():
    """Кнопка отмены редактирования встречи."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Отмена",
        callback_data="meeting_edit_cancel",
    ))
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
