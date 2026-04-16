import logging
from aiogram import Bot
from django.conf import settings
from core.models import TelegramUser

logger = logging.getLogger(__name__)


async def send_notification(user: TelegramUser, text: str, parse_mode: str = "HTML"):
    """Отправляет уведомление пользователю в личку"""
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(chat_id=user.telegram_id, text=text, parse_mode=parse_mode)
        logger.info(f"Notification sent to {user.telegram_id}")
    except Exception as e:
        logger.error(f"Failed to send notification to {user.telegram_id}: {e}")
    finally:
        await bot.session.close()


async def send_reminder(user: TelegramUser, reminder_text: str):
    await send_notification(user, f"🔔 <b>Напоминание</b>\n{reminder_text}")