from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton


def task_actions_keyboard(task_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="Выполнено",
        callback_data=f"task_done:{task_id}"
    ))
    builder.add(InlineKeyboardButton(
        text="Подробнее",
        callback_data=f"task_detail:{task_id}"
    ))
    return builder.as_markup()


def confirm_keyboard(action: str, item_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="Да",
        callback_data=f"confirm:{action}:{item_id}"
    ))
    builder.add(InlineKeyboardButton(
        text="Нет",
        callback_data="cancel"
    ))
    return builder.as_markup()