from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton


# ─────────────────────────────────────────────────────────────────────
# Задачи
# ─────────────────────────────────────────────────────────────────────


def task_keyboard(task_id: int, has_recurrence: bool = False):
    """Кнопки 'Выполнено' и 'Редактировать' под каждой задачей.
       Если задача — часть серии, добавляем 'Отменить серию'."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✅ Выполнено",
        callback_data=f"task_done:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="✏️ Редактировать",
        callback_data=f"task_edit:{task_id}",
    ))
    if has_recurrence:
        builder.add(InlineKeyboardButton(
            text="🛑 Отменить серию",
            callback_data=f"task_cancel_series:{task_id}",
        ))
        builder.adjust(2, 1)
    else:
        builder.adjust(2)
    return builder.as_markup()


def task_edit_options_keyboard(task_id: int, has_recurrence: bool = False):
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
    if has_recurrence:
        builder.add(InlineKeyboardButton(
            text="🔄 Редактировать серию",
            callback_data=f"task_edit_series:{task_id}",
        ))
        builder.adjust(2, 2)
    else:
        builder.adjust(2)
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


def task_cancel_series_confirm_keyboard(task_id: int):
    """Подтверждение отмены всей серии задач."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✅ Да, отменить серию",
        callback_data=f"task_cancel_series_confirm:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Нет, оставить",
        callback_data=f"task_back:{task_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────────────
# Встречи
# ─────────────────────────────────────────────────────────────────────


def meeting_keyboard(meeting_id: int, has_recurrence: bool = False):
    """Кнопки 'Редактировать' и 'Отменить' под каждой встречей.
       Если встреча часть серии — добавляем кнопку отмены всей серии."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✏️ Редактировать",
        callback_data=f"meeting_edit:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="❌ Отменить",
        callback_data=f"meeting_cancel:{meeting_id}",
    ))
    if has_recurrence:
        builder.add(InlineKeyboardButton(
            text="🛑 Отменить всю серию",
            callback_data=f"meeting_cancel_series:{meeting_id}",
        ))
        builder.adjust(2, 1)
    else:
        builder.adjust(2)
    return builder.as_markup()


def meeting_cancel_choice_keyboard(meeting_id: int, recurrence_id: int):
    """Выбор: отменить одну встречу или всю серию."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="🗑 Отменить только эту",
        callback_data=f"meeting_cancel_single:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="🛑 Отменить всю серию",
        callback_data=f"meeting_cancel_series:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Назад",
        callback_data=f"meeting_back_single:{meeting_id}",
    ))
    builder.adjust(2, 1)
    return builder.as_markup()


def meeting_edit_options_keyboard(meeting_id: int, has_recurrence: bool = False):
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
    if has_recurrence:
        builder.add(InlineKeyboardButton(
            text="🔄 Редактировать серию",
            callback_data=f"meeting_edit_series:{meeting_id}",
        ))
        builder.adjust(2, 2)
    else:
        builder.adjust(2)
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
