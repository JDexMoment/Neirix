"""Отложенные уведомления (тихие часы / выходные)."""
import logging
from celery import shared_task
from django.conf import settings
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


async def _send_deferred_async(user_telegram_id: int, text: str,
                               parse_mode: str = "HTML",
                               message_thread_id: int | None = None):
    from aiogram import Bot
    from core.models import TelegramUser
    from asgiref.sync import sync_to_async

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        user = await sync_to_async(
            lambda: TelegramUser.objects.filter(telegram_id=user_telegram_id).first()
        )()
        if not user:
            logger.warning("Deferred: user %s not found", user_telegram_id)
            return False

        # Повторная проверка: пользователь мог стать недоступным за ночь
        from core.services.notification_policy import evaluate, Decision
        decision = await evaluate(user)
        if decision == Decision.SKIP:
            logger.info("Deferred notification dropped (user absent) | user=%s", user_telegram_id)
            return False
        if decision == Decision.DEFER:
            # Всё ещё тихие часы (например, отложенное на 8:00, а пользователь сменил настройки)
            from core.services.notification_policy import next_delivery_time
            from celery_app.tasks.deferred_notifications import send_deferred_notification
            eta = await next_delivery_time()
            send_deferred_notification.apply_async(
                kwargs={
                    "user_telegram_id": user_telegram_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "message_thread_id": message_thread_id,
                },
                eta=eta,
            )
            return False

        kwargs = {"chat_id": user_telegram_id, "text": text, "parse_mode": parse_mode}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        await bot.send_message(**kwargs)
        logger.info("Deferred notification delivered | user=%s", user_telegram_id)
        return True
    except Exception as e:
        logger.error("Deferred notification failed | user=%s: %s", user_telegram_id, e)
        return False
    finally:
        await bot.session.close()


@shared_task(name="celery_app.tasks.deferred_notifications.send_deferred_notification")
def send_deferred_notification(user_telegram_id: int, text: str,
                               parse_mode: str = "HTML",
                               message_thread_id: int | None = None):
    return async_to_sync(_send_deferred_async)(
        user_telegram_id, text, parse_mode, message_thread_id,
    )