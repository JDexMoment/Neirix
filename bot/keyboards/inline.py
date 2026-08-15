from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton


# ─────────────────────────────────────────────────────────────────────
# Задачи
# ─────────────────────────────────────────────────────────────────────


def task_keyboard(task_id: int, has_recurrence: bool = False, comment_count: int = 0, has_subtasks: bool = False):
    builder = InlineKeyboardBuilder()
    if has_subtasks:
        builder.add(InlineKeyboardButton(
            text="🗂 Подзадачи",
            callback_data=f"task_subs:{task_id}",
        ))
    else:
        builder.add(InlineKeyboardButton(
            text="✅ Выполнено",
            callback_data=f"task_done:{task_id}",
        ))
    builder.add(InlineKeyboardButton(
        text="✏️ Редактировать",
        callback_data=f"task_edit:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="➕ Подзадача",
        callback_data=f"task_add_subtask:{task_id}",
    ))
    if has_recurrence:
        builder.add(InlineKeyboardButton(
            text="🛑 Отменить серию",
            callback_data=f"task_cancel_series:{task_id}",
        ))
    if comment_count > 0:
        builder.add(InlineKeyboardButton(
            text=f"💬 {comment_count}",
            callback_data=f"task_info:{task_id}",
        ))
    # Раскладка: первая строка — 2 кнопки, дальше по одной
    n_extra = (1 if has_recurrence else 0) + (1 if comment_count > 0 else 0)
    builder.adjust(2, *([1] * (1 + n_extra)))
    return builder.as_markup()


def task_edit_options_keyboard(task_id: int, has_recurrence: bool = False):
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
        text="🔽 Приоритет",
        callback_data=f"task_cycle_priority:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Назад к задаче",
        callback_data=f"task_back:{task_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def task_edit_cancel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Отмена",
        callback_data="task_edit_cancel",
    ))
    return builder.as_markup()


def task_assign_keyboard(task_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="👤 Назначить исполнителя",
        callback_data=f"task_edit_assignee:{task_id}",
    ))
    return builder.as_markup()


def task_cancel_series_confirm_keyboard(task_id: int):
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


def task_edit_series_choice_keyboard(task_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✏️ Только эту",
        callback_data=f"task_edit_single:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="🔄 Всю серию",
        callback_data=f"task_edit_series:{task_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Назад",
        callback_data=f"task_back:{task_id}",
    ))
    builder.adjust(2, 1)
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────────────
# Встречи
# ─────────────────────────────────────────────────────────────────────


def meeting_keyboard(meeting_id: int, has_recurrence: bool = False, comment_count: int = 0):
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
    if comment_count > 0:
        builder.add(InlineKeyboardButton(
            text=f"💬 {comment_count}",
            callback_data=f"meeting_info:{meeting_id}",
        ))
    if has_recurrence:
        builder.adjust(2, 1, 1) if comment_count > 0 else builder.adjust(2, 1)
    else:
        builder.adjust(2, 1) if comment_count > 0 else builder.adjust(2)
    return builder.as_markup()


def meeting_cancel_choice_keyboard(meeting_id: int, recurrence_id: int):
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
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Отмена",
        callback_data="meeting_edit_cancel",
    ))
    return builder.as_markup()


def meeting_cancel_confirm_keyboard(meeting_id: int):
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


def meeting_cancel_series_confirm_keyboard(meeting_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="🛑 Да, отменить всю серию",
        callback_data=f"meeting_cancel_series_confirm:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Нет, оставить",
        callback_data=f"meeting_cancel_abort:{meeting_id}",
    ))
    builder.adjust(2)
    return builder.as_markup()


def meeting_edit_series_choice_keyboard(meeting_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✏️ Только эту",
        callback_data=f"meeting_edit_single:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="🔄 Всю серию",
        callback_data=f"meeting_edit_series:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="↩️ Назад",
        callback_data=f"meeting_back_single:{meeting_id}",
    ))
    builder.adjust(2, 1)
    return builder.as_markup()


def meeting_reschedule_cancel_keyboard():
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


# ─────────────────────────────────────────────────────────────────────
# Настройки уведомлений (/settings)
# ─────────────────────────────────────────────────────────────────────


def settings_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="🔔 Время напоминания о встрече",
        callback_data="settings_cycle_meeting_time",
    ))
    builder.add(InlineKeyboardButton(
        text="📅 Дайджест: вкл/выкл",
        callback_data="settings_toggle_digest",
    ))
    builder.add(InlineKeyboardButton(
        text="🕐 Время дайджеста",
        callback_data="settings_cycle_digest_time",
    ))
    builder.add(InlineKeyboardButton(
        text="📝 Напоминания о задачах: вкл/выкл",
        callback_data="settings_toggle_task",
    ))
    builder.add(InlineKeyboardButton(
        text="🔔 Напоминания о встречах: вкл/выкл",
        callback_data="settings_toggle_meeting",
    ))
    builder.adjust(1)
    return builder.as_markup()


def settings_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Назад к настройкам",
        callback_data="settings_back",
    ))
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────────────
# Подтверждение участия во встречах
# ─────────────────────────────────────────────────────────────────────


def meeting_confirmation_keyboard(meeting_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="✅ Буду",
        callback_data=f"meeting_confirm:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="❌ Не смогу",
        callback_data=f"meeting_decline:{meeting_id}",
    ))
    builder.add(InlineKeyboardButton(
        text="📋 Кто идёт?",
        callback_data=f"meeting_attendance_status:{meeting_id}",
    ))
    builder.adjust(2, 1)
    return builder.as_markup()


def meeting_attendance_status_keyboard(meeting_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="↩️ Назад к встрече",
        callback_data=f"meeting_back_single:{meeting_id}",
    ))
    return builder.as_markup()
