"""
Сервис для работы с настройками уведомлений пользователя.
"""
import logging
from datetime import time as dtime
from typing import Optional

from asgiref.sync import sync_to_async

from core.models import TelegramUser, UserNotificationSettings

logger = logging.getLogger(__name__)


@sync_to_async
def get_or_create_settings(user: TelegramUser) -> UserNotificationSettings:
    """Получает или создаёт настройки для пользователя."""
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    return settings


@sync_to_async
def toggle_meeting_reminders(user: TelegramUser) -> bool:
    """Вкл/выкл напоминания о встречах. Возвращает новое состояние."""
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    settings.meeting_reminder_enabled = not settings.meeting_reminder_enabled
    settings.save(update_fields=["meeting_reminder_enabled"])
    return settings.meeting_reminder_enabled


@sync_to_async
def toggle_task_reminders(user: TelegramUser) -> bool:
    """Вкл/выкл напоминания о задачах."""
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    settings.task_reminder_enabled = not settings.task_reminder_enabled
    settings.save(update_fields=["task_reminder_enabled"])
    return settings.task_reminder_enabled


@sync_to_async
def toggle_digest(user: TelegramUser) -> bool:
    """Вкл/выкл ежедневный дайджест."""
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    settings.digest_enabled = not settings.digest_enabled
    settings.save(update_fields=["digest_enabled"])
    return settings.digest_enabled


_MEETING_TIMES = [15, 60, 1440]


@sync_to_async
def cycle_meeting_reminder_time(user: TelegramUser) -> int:
    """Циклически переключает время напоминания о встрече."""
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    current = settings.meeting_reminder_minutes
    try:
        idx = _MEETING_TIMES.index(current)
        next_idx = (idx + 1) % len(_MEETING_TIMES)
    except ValueError:
        next_idx = 0
    new_value = _MEETING_TIMES[next_idx]
    settings.meeting_reminder_minutes = new_value
    settings.save(update_fields=["meeting_reminder_minutes"])
    return new_value


_DIGEST_TIMES = [dtime(8, 0), dtime(9, 0), dtime(10, 0), dtime(11, 0)]


@sync_to_async
def cycle_digest_time(user: TelegramUser) -> dtime:
    """Циклически переключает время дайджеста."""
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    current = settings.digest_time
    try:
        idx = _DIGEST_TIMES.index(current)
        next_idx = (idx + 1) % len(_DIGEST_TIMES)
    except (ValueError, AttributeError):
        next_idx = 0
    new_value = _DIGEST_TIMES[next_idx]
    settings.digest_time = new_value
    settings.save(update_fields=["digest_time"])
    return new_value


def format_meeting_reminder_minutes(minutes: int) -> str:
    if minutes == 15:
        return "за 15 минут"
    elif minutes == 60:
        return "за 1 час"
    elif minutes == 1440:
        return "за 1 день"
    return f"за {minutes} мин"


def format_digest_time(t: Optional[dtime]) -> str:
    if t is None:
        return "не настроено"
    return t.strftime("%H:%M")


def format_settings_text(settings: UserNotificationSettings) -> str:
    """Форматирует текст с настройками для сообщения."""
    meeting_status = "✅" if settings.meeting_reminder_enabled else "❌"
    digest_status = "✅" if settings.digest_enabled else "❌"
    task_status = "✅" if settings.task_reminder_enabled else "❌"

    meeting_time_str = format_meeting_reminder_minutes(settings.meeting_reminder_minutes)
    digest_time_str = format_digest_time(settings.digest_time)

    quiet_status = "✅" if settings.quiet_hours_enabled else "❌"
    quiet_range = f"{settings.quiet_start.strftime('%H:%M')}–{settings.quiet_end.strftime('%H:%M')}"
    weekend_status = "✅" if settings.skip_weekends else "❌"

    return (
        f"⚙️ <b>Настройки уведомлений</b>\n\n"
        f"{meeting_status} Напоминания о встречах — {meeting_time_str}\n"
        f"{digest_status} Ежедневный дайджест — {digest_time_str}\n"
        f"{task_status} Напоминания о задачах\n\n"
        f"<i>Нажми на кнопку, чтобы изменить настройку</i>"
        f"{quiet_status} Тихие часы — {quiet_range}\n"
        f"{weekend_status} Пропуск выходных"
    )

@sync_to_async
def toggle_quiet_hours(user: TelegramUser) -> bool:
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    settings.quiet_hours_enabled = not settings.quiet_hours_enabled
    settings.save(update_fields=["quiet_hours_enabled"])
    return settings.quiet_hours_enabled


_QUIET_PRESETS = [
    (dtime(20, 0), dtime(8, 0)),
    (dtime(22, 0), dtime(9, 0)),
    (dtime(23, 0), dtime(7, 0)),
    (dtime(21, 0), dtime(10, 0)),
]


@sync_to_async
def cycle_quiet_hours(user: TelegramUser) -> tuple:
    """Циклически переключает пресеты тихих часов."""
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    current = (settings.quiet_start, settings.quiet_end)
    try:
        idx = _QUIET_PRESETS.index(current)
        next_idx = (idx + 1) % len(_QUIET_PRESETS)
    except ValueError:
        next_idx = 0
    settings.quiet_start, settings.quiet_end = _QUIET_PRESETS[next_idx]
    settings.save(update_fields=["quiet_start", "quiet_end"])
    return settings.quiet_start, settings.quiet_end


@sync_to_async
def toggle_skip_weekends(user: TelegramUser) -> bool:
    settings, _ = UserNotificationSettings.objects.get_or_create(user=user)
    settings.skip_weekends = not settings.skip_weekends
    settings.save(update_fields=["skip_weekends"])
    return settings.skip_weekends