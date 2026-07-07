import asyncio
import logging
import re
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from aiogram import Bot
from asgiref.sync import sync_to_async

from core.models import Meeting, Task, TaskAssignee, TelegramUser
from bot.services.notification_sender import NotificationSender

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _is_bot_user(user: TelegramUser) -> bool:
    if hasattr(user, "is_bot") and user.is_bot:
        return True
    if user.username and re.search(r"[_]?[Bb]ot$", user.username):
        return True
    return False


# ═════════════════════════════════════════════════════════════════════
#  Окна поиска
# ═════════════════════════════════════════════════════════════════════

# «за 1 час»:  50–70 мин  (раньше 55–65)
MEETING_1H_WINDOW = (50, 70)

# «за 24 часа»: 22–26 ч  (раньше 23–25)
MEETING_24H_WINDOW = (22, 26)


# ─────────────────────────────────────────────────────────────────────
#  Напоминание о встрече за ~1 час
# ─────────────────────────────────────────────────────────────────────

async def _send_meeting_1h_reminders_async():
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    sender = NotificationSender(bot)
    try:
        now = timezone.now()
        window_start = now + timedelta(minutes=MEETING_1H_WINDOW[0])
        window_end = now + timedelta(minutes=MEETING_1H_WINDOW[1])

        meetings = await sync_to_async(list)(
            Meeting.objects.filter(
                status="active",
                reminder_sent=False,
                start_at__gte=window_start,
                start_at__lte=window_end,
            )
            .prefetch_related("participants")
            .select_related("topic__chat")
        )

        sent_count = 0
        for meeting in meetings:
            participants = await sync_to_async(list)(meeting.participants.all())
            for user in participants:
                if _is_bot_user(user):
                    continue
                if await sender.send_meeting_in_1_hour(user, meeting):
                    sent_count += 1

            meeting.reminder_sent = True
            await sync_to_async(meeting.save)(update_fields=["reminder_sent"])
            logger.info("Meeting 1h reminder sent | meeting_id=%s", meeting.id)

        logger.info("Meeting 1h reminders total: %s", sent_count)
        return sent_count
    finally:
        await bot.session.close()


# ─────────────────────────────────────────────────────────────────────
#  Напоминание о встрече за ~24 часа
# ─────────────────────────────────────────────────────────────────────

async def _send_meeting_24h_reminders_async():
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    sender = NotificationSender(bot)
    try:
        now = timezone.now()
        window_start = now + timedelta(hours=MEETING_24H_WINDOW[0])
        window_end = now + timedelta(hours=MEETING_24H_WINDOW[1])

        meetings = await sync_to_async(list)(
            Meeting.objects.filter(
                status="active",
                daily_reminder_sent=False,
                start_at__gte=window_start,
                start_at__lte=window_end,
            )
            .prefetch_related("participants")
            .select_related("topic__chat")
        )

        sent_count = 0
        for meeting in meetings:
            participants = await sync_to_async(list)(meeting.participants.all())
            for user in participants:
                if _is_bot_user(user):
                    continue
                if await sender.send_meeting_in_24_hours(user, meeting):
                    sent_count += 1

            meeting.daily_reminder_sent = True
            await sync_to_async(meeting.save)(update_fields=["daily_reminder_sent"])
            logger.info("Meeting 24h reminder sent | meeting_id=%s", meeting.id)

        logger.info("Meeting 24h reminders total: %s", sent_count)
        return sent_count
    finally:
        await bot.session.close()


# ─────────────────────────────────────────────────────────────────────
#  Напоминание о задаче с дедлайном «завтра»
# ─────────────────────────────────────────────────────────────────────

async def _send_task_24h_reminders_async():
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    sender = NotificationSender(bot)
    try:
        now = timezone.now()
        tomorrow_start = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        tomorrow_end = tomorrow_start + timedelta(days=1)

        tasks = await sync_to_async(list)(
            Task.objects.filter(
                status="open",
                daily_reminder_sent=False,
                due_date__gte=tomorrow_start,
                due_date__lt=tomorrow_end,
            )
            .prefetch_related("assignees__user")
            .select_related("topic__chat")
        )

        sent_count = 0
        for task in tasks:
            assignees = await sync_to_async(
                lambda: [ta.user for ta in task.assignees.all()]
            )()
            for user in assignees:
                if _is_bot_user(user):
                    continue
                if await sender.send_task_in_24_hours(user, task):
                    sent_count += 1

            task.daily_reminder_sent = True
            await sync_to_async(task.save)(update_fields=["daily_reminder_sent"])
            logger.info("Task 24h reminder sent | task_id=%s", task.id)

        logger.info("Task 24h reminders total: %s", sent_count)
        return sent_count
    finally:
        await bot.session.close()


# ─────────────────────────────────────────────────────────────────────
#  Напоминание о просроченных задачах (один раз)
# ─────────────────────────────────────────────────────────────────────

async def _send_overdue_task_reminders_async():
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    sender = NotificationSender(bot)
    try:
        now = timezone.now()

        tasks = await sync_to_async(list)(
            Task.objects.filter(
                status="open",
                overdue_reminder_sent=False,
                due_date__lt=now,
            )
            .prefetch_related("assignees__user")
            .select_related("topic__chat")
        )

        sent_count = 0
        for task in tasks:
            assignees = await sync_to_async(
                lambda: [ta.user for ta in task.assignees.all()]
            )()
            for user in assignees:
                if _is_bot_user(user):
                    continue
                if await sender.send_task_overdue(user, task):
                    sent_count += 1

            task.overdue_reminder_sent = True
            await sync_to_async(task.save)(update_fields=["overdue_reminder_sent"])
            logger.info("Overdue reminder sent | task_id=%s", task.id)

        logger.info("Overdue task reminders total: %s", sent_count)
        return sent_count
    finally:
        await bot.session.close()


# ─────────────────────────────────────────────────────────────────────
#  Утренний дайджест
# ─────────────────────────────────────────────────────────────────────

async def _send_daily_digest_async():
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    sender = NotificationSender(bot)
    try:
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        sent_count = 0

        # ── Задачи на сегодня ──────────────────────────────────────
        today_task_links = await sync_to_async(list)(
            TaskAssignee.objects.filter(
                task__status="open",
                task__due_date__gte=today_start,
                task__due_date__lt=today_end,
            )
            .select_related("task", "user", "task__topic__chat")
            .order_by("task__due_date")
        )

        for ta in today_task_links:
            if _is_bot_user(ta.user):
                continue
            if await sender.send_task_today(ta.user, ta.task):
                sent_count += 1

        # ── Встречи на сегодня ─────────────────────────────────────
        today_meetings = await sync_to_async(list)(
            Meeting.objects.filter(
                status="active",
                start_at__gte=today_start,
                start_at__lt=today_end,
            )
            .prefetch_related("participants")
            .select_related("topic__chat")
            .order_by("start_at")
        )

        for meeting in today_meetings:
            participants = await sync_to_async(list)(meeting.participants.all())
            for user in participants:
                if _is_bot_user(user):
                    continue
                if await sender.send_meeting_today(user, meeting):
                    sent_count += 1

        logger.info("Daily digest sent: %s notifications", sent_count)
        return sent_count
    finally:
        await bot.session.close()


# ═════════════════════════════════════════════════════════════════════
#  Celery-задачи
# ═════════════════════════════════════════════════════════════════════

@shared_task(name="celery_app.tasks.send_reminders.send_meeting_reminders")
def send_meeting_reminders():
    return _run_async(_send_meeting_1h_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_meeting_24h_reminders")
def send_meeting_24h_reminders():
    return _run_async(_send_meeting_24h_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_task_24h_reminders")
def send_task_24h_reminders():
    return _run_async(_send_task_24h_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_overdue_task_reminders")
def send_overdue_task_reminders():
    return _run_async(_send_overdue_task_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_daily_digest")
def send_daily_digest():
    return _run_async(_send_daily_digest_async())
