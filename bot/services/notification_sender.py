import logging
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from django.utils import timezone

from core.models import TelegramUser, Task, Meeting

logger = logging.getLogger(__name__)


class NotificationSender:
    """Сервис отправки уведомлений через Telegram."""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_notification(
        self,
        user: TelegramUser,
        text: str,
        parse_mode: str = "HTML",
    ) -> bool:
        if not user or not user.telegram_id:
            return False

        try:
            await self.bot.send_message(
                chat_id=user.telegram_id,
                text=text,
                parse_mode=parse_mode,
            )
            logger.info("Notification sent to user %s", user.telegram_id)
            return True

        except TelegramForbiddenError:
            logger.warning("User %s blocked bot", user.telegram_id)
            return False

        except TelegramBadRequest as e:
            logger.error("Bad request sending to %s: %s", user.telegram_id, e)
            return False

        except Exception as e:
            logger.error("Failed to send to %s: %s", user.telegram_id, e, exc_info=True)
            return False

    async def send_reminder(self, user: TelegramUser, reminder_text: str) -> bool:
        return await self.send_notification(
            user,
            f"🔔 <b>Напоминание</b>\n\n{reminder_text}",
        )

    # ─────────────────────────────────────────────────────────────────
    # Встречи
    # ─────────────────────────────────────────────────────────────────

    async def send_meeting_in_1_hour(self, user: TelegramUser, meeting: Meeting) -> bool:
        """Напоминание за 1 час до встречи."""
        local_time = timezone.localtime(meeting.start_at) if timezone.is_aware(meeting.start_at) else meeting.start_at
        text = (
            f"⏰ <b>Встреча через 1 час!</b>\n\n"
            f"📌 {meeting.title}\n"
            f"🕐 {local_time.strftime('%H:%M')}"
        )
        return await self.send_notification(user, text)

    async def send_meeting_in_24_hours(self, user: TelegramUser, meeting: Meeting) -> bool:
        """Напоминание за сутки до встречи."""
        local_time = timezone.localtime(meeting.start_at) if timezone.is_aware(meeting.start_at) else meeting.start_at
        text = (
            f"📅 <b>Встреча завтра</b>\n\n"
            f"📌 {meeting.title}\n"
            f"🕐 {local_time.strftime('%d.%m.%Y %H:%M')}"
        )
        return await self.send_notification(user, text)

    async def send_meeting_today(self, user: TelegramUser, meeting: Meeting) -> bool:
        """Утреннее напоминание о встрече сегодня."""
        local_time = timezone.localtime(meeting.start_at) if timezone.is_aware(meeting.start_at) else meeting.start_at
        text = (
            f"📅 <b>Встреча сегодня</b>\n\n"
            f"📌 {meeting.title}\n"
            f"⏰ {local_time.strftime('%H:%M')}"
        )
        return await self.send_notification(user, text)

    async def send_meeting_cancelled(self, user: TelegramUser, meeting: Meeting) -> bool:
        """Уведомление об отмене встречи."""
        text = (
            f"❌ <b>Встреча отменена</b>\n\n"
            f"📌 {meeting.title}"
        )
        return await self.send_notification(user, text)

    async def send_meeting_rescheduled(
        self,
        user: TelegramUser,
        meeting: Meeting,
        old_start_at,
    ) -> bool:
        """Уведомление о переносе встречи."""
        old_local = timezone.localtime(old_start_at) if timezone.is_aware(old_start_at) else old_start_at
        new_local = timezone.localtime(meeting.start_at) if timezone.is_aware(meeting.start_at) else meeting.start_at
        text = (
            f"📅 <b>Встреча перенесена</b>\n\n"
            f"📌 {meeting.title}\n"
            f"Было: {old_local.strftime('%d.%m.%Y %H:%M')}\n"
            f"Стало: {new_local.strftime('%d.%m.%Y %H:%M')}"
        )
        return await self.send_notification(user, text)

    # ─────────────────────────────────────────────────────────────────
    # Задачи
    # ─────────────────────────────────────────────────────────────────

    async def send_task_in_24_hours(self, user: TelegramUser, task: Task) -> bool:
        """Напоминание: дедлайн через сутки."""
        if task.due_date:
            local_due = timezone.localtime(task.due_date) if timezone.is_aware(task.due_date) else task.due_date
            due_str = local_due.strftime('%d.%m.%Y %H:%M')
        else:
            due_str = "без срока"

        text = (
            f"⏰ <b>Дедлайн завтра!</b>\n\n"
            f"📌 {task.title}\n"
            f"📅 {due_str}"
        )
        return await self.send_notification(user, text)

    async def send_task_today(self, user: TelegramUser, task: Task) -> bool:
        """Утреннее напоминание о задаче на сегодня."""
        if task.due_date:
            local_due = timezone.localtime(task.due_date) if timezone.is_aware(task.due_date) else task.due_date
            due_str = local_due.strftime('%H:%M')
        else:
            due_str = "сегодня"

        text = (
            f"📋 <b>Задача на сегодня</b>\n\n"
            f"📌 {task.title}\n"
            f"⏰ до {due_str}"
        )
        return await self.send_notification(user, text)

    async def send_task_overdue(self, user: TelegramUser, task: Task) -> bool:
        """Напоминание о просроченной задаче (отправляется один раз)."""
        if task.due_date:
            local_due = timezone.localtime(task.due_date) if timezone.is_aware(task.due_date) else task.due_date
            due_str = local_due.strftime('%d.%m.%Y %H:%M')
        else:
            due_str = "без срока"

        text = (
            f"⚠️ <b>Задача просрочена!</b>\n\n"
            f"📌 {task.title}\n"
            f"📅 Дедлайн был: {due_str}\n\n"
            f"Пожалуйста, обновите статус задачи."
        )
        return await self.send_notification(user, text)