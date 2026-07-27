"""
Хендлер для настроек уведомлений (/settings).
Работает только в личных сообщениях (ЛС).
"""
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from asgiref.sync import sync_to_async

from bot.utils import get_chat_context
from bot.keyboards.inline import (
    settings_main_keyboard,
    settings_back_keyboard,
)
from core.services.notification_settings import (
    get_or_create_settings,
    toggle_meeting_reminders,
    toggle_task_reminders,
    toggle_digest,
    cycle_meeting_reminder_time,
    cycle_digest_time,
    format_settings_text,
)

from core.models import TelegramUser

logger = logging.getLogger(__name__)
router = Router()


async def _get_db_user_from_callback(callback: CallbackQuery) -> TelegramUser:
    """Получает db_user по callback.from_user.id (минуя get_chat_context)."""
    telegram_id = callback.from_user.id
    user = await sync_to_async(
        lambda: TelegramUser.objects.filter(telegram_id=telegram_id).first()
    )()
    return user


async def _show_settings(message_or_callback, user):
    """Показывает главное меню настроек."""
    settings = await get_or_create_settings(user)
    text = format_settings_text(settings)

    if hasattr(message_or_callback, 'message') and message_or_callback.message:
        # Это CallbackQuery
        await message_or_callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=settings_main_keyboard(),
        )
        await message_or_callback.answer()
    else:
        # Это Message
        await message_or_callback.answer(
            text,
            parse_mode="HTML",
            reply_markup=settings_main_keyboard(),
        )


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    """Обрабатывает команду /settings (только в ЛС)."""
    if message.chat.type != "private":
        await message.answer("⚙️ Настройки доступны только в личных сообщениях с ботом.")
        return

    chat, topic, db_user = await get_chat_context(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    await _show_settings(message, db_user)


async def _handle_settings_callback(callback: CallbackQuery, action):
    """Общий обработчик для всех callback'ов настроек."""
    db_user = await _get_db_user_from_callback(callback)
    if not db_user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    await action(db_user)
    await _show_settings(callback, db_user)


@router.callback_query(F.data == "settings_back")
async def callback_settings_back(callback: CallbackQuery):
    """Возвращает в главное меню настроек."""
    await _handle_settings_callback(callback, lambda u: None)


@router.callback_query(F.data == "settings_toggle_meeting")
async def callback_toggle_meeting(callback: CallbackQuery):
    """Вкл/выкл напоминания о встречах."""
    async def action(user):
        new_state = await toggle_meeting_reminders(user)
        logger.info("Meeting reminders toggled for user %s: %s", user.id, new_state)
    await _handle_settings_callback(callback, action)


@router.callback_query(F.data == "settings_toggle_task")
async def callback_toggle_task(callback: CallbackQuery):
    """Вкл/выкл напоминания о задачах."""
    async def action(user):
        new_state = await toggle_task_reminders(user)
        logger.info("Task reminders toggled for user %s: %s", user.id, new_state)
    await _handle_settings_callback(callback, action)


@router.callback_query(F.data == "settings_toggle_digest")
async def callback_toggle_digest(callback: CallbackQuery):
    """Вкл/выкл дайджест."""
    async def action(user):
        new_state = await toggle_digest(user)
        logger.info("Digest toggled for user %s: %s", user.id, new_state)
    await _handle_settings_callback(callback, action)


@router.callback_query(F.data == "settings_cycle_meeting_time")
async def callback_cycle_meeting_time(callback: CallbackQuery):
    """Циклически меняет время напоминания о встрече."""
    async def action(user):
        new_value = await cycle_meeting_reminder_time(user)
        logger.info("Meeting reminder time changed for user %s: %d min", user.id, new_value)
    await _handle_settings_callback(callback, action)


@router.callback_query(F.data == "settings_cycle_digest_time")
async def callback_cycle_digest_time(callback: CallbackQuery):
    """Циклически меняет время дайджеста."""
    async def action(user):
        new_value = await cycle_digest_time(user)
        logger.info("Digest time changed for user %s: %s", user.id, new_value)
    await _handle_settings_callback(callback, action)
