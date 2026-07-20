"""
Celery-задача: вынос NLP-обработки из бота в воркер.

Зачем:
  - Бот не блокируется на время GigaChat (1-3 сек)
  - Очередь сообщений не растёт
  - Можно горизонтально масштабировать воркеры

Как работает:
  1. Бот сохраняет сообщение в БД и вызывает .delay()
  2. Celery worker забирает задачу, создаёт Bot, делает всё, отправляет ответ
"""
import asyncio
import logging

from celery import shared_task
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


@shared_task(name="process_nlp_message", bind=True, max_retries=2, default_retry_delay=5)
def process_nlp_message(
    self,
    chat_id: int,
    user_telegram_id: int,
    text: str,
    db_message_id: int,
    telegram_msg_id: int,
    message_thread_id: int = 0,
):
    """
    Celery-задача: обрабатывает ЛС через GigaChat.

    Параметры:
      chat_id — Telegram chat.id
      user_telegram_id — Telegram from_user.id
      text — текст сообщения
      db_message_id — id из core_models.Message (уже сохранён)
      telegram_msg_id — telegram message_id
      message_thread_id — thread_id для форума (0 если нет)
    """
    try:
        from core.services.nlp_processor import process_nlp_message_standalone
        from aiogram import Bot
        from django.conf import settings

        async def _run():
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            try:
                result = await process_nlp_message_standalone(
                    bot=bot,
                    chat_id=chat_id,
                    user_telegram_id=user_telegram_id,
                    text=text,
                    db_message_id=db_message_id,
                    telegram_msg_id=telegram_msg_id,
                    message_thread_id=message_thread_id,
                )
                return result
            finally:
                await bot.session.close()

        return asyncio.run(_run())

    except Exception as exc:
        logger.error("NLP processing failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
