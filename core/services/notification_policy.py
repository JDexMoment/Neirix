"""
Политика доставки уведомлений: тихие часы, выходные, недоступность.
Решение принимается ДО отправки в NotificationSender.
"""
import logging
from datetime import datetime, timedelta, time as dtime
from enum import Enum
from django.utils import timezone
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

# Границы по умолчанию (если у пользователя нет настроек)
DEFAULT_QUIET_START = dtime(20, 0)
DEFAULT_QUIET_END = dtime(8, 0)


class Decision(str, Enum):
    SEND = "send"      # отправить сейчас
    DEFER = "defer"    # отложить до утра буднего дня
    SKIP = "skip"      # не отправлять вообще (пользователь недоступен)


def is_quiet_hours_sync(dt: datetime, quiet_start, quiet_end) -> bool:
    """Проверяет, попадает ли момент в тихие часы.
    Поддерживает переход через полночь (20:00 → 8:00)."""
    t = dt.time()
    if quiet_start <= quiet_end:
        # Обычный диапазон внутри дня (например 13:00-14:00)
        return quiet_start <= t <= quiet_end
    # Переход через полночь (20:00 → 8:00)
    return t >= quiet_start or t < quiet_end


def next_delivery_time_sync(now: datetime = None) -> datetime:
    """Ближайшее время доставки: 8:00 ближайшего буднего дня."""
    now = now or timezone.localtime(timezone.now())
    candidate = now.replace(
        hour=DEFAULT_QUIET_END.hour,
        minute=DEFAULT_QUIET_END.minute,
        second=0, microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    # Пропускаем выходные
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def evaluate_sync(user, now: datetime = None) -> Decision:
    """Синхронная версия: решение для конкретного пользователя."""
    from core.models import UserNotificationSettings
    from core.services.absence_service import is_absent_now_sync

    now = now or timezone.localtime(timezone.now())

    # 1. Недоступность → не слать вообще
    if is_absent_now_sync(user, now):
        return Decision.SKIP

    # 2. Настройки тихих часов
    try:
        settings = user.notif_settings
    except UserNotificationSettings.DoesNotExist:
        settings = None

    if settings and not settings.quiet_hours_enabled:
        return Decision.SEND

    quiet_start = settings.quiet_start if settings else DEFAULT_QUIET_START
    quiet_end = settings.quiet_end if settings else DEFAULT_QUIET_END
    skip_weekends = settings.skip_weekends if settings else True

    # Выходные
    if skip_weekends and now.weekday() >= 5:
        return Decision.DEFER

    # Тихие часы
    if is_quiet_hours_sync(now, quiet_start, quiet_end):
        return Decision.DEFER

    return Decision.SEND


async def evaluate(user, now: datetime = None) -> Decision:
    """Асинхронная обёртка — единственная точка входа."""
    return await sync_to_async(evaluate_sync)(user, now)


async def next_delivery_time(now: datetime = None) -> datetime:
    return await sync_to_async(next_delivery_time_sync)(now)