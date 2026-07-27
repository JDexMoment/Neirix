import asyncio
import logging
import re
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from aiogram import Bot
from asgiref.sync import sync_to_async

from core.models import Meeting, Task, TaskAssignee, TelegramUser, UserNotificationSettings
from bot.keyboards.inline import meeting_confirmation_keyboard
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
#  Универсальное напоминание о встрече (с учётом настроек пользователя)
# ═════════════════════════════════════════════════════════════════════

async def _send_meeting_reminders_by_window_async(window_minutes: int, tolerance: int = 10):
    """
    Отправляет напоминания о встречах, которые начнутся через ~window_minutes минут.
    Для каждого пользователя проверяет его настройки (meeting_reminder_minutes).
    Если у пользователя стоит другое время — пропускаем (он получит напоминание в другой таске).

    window_minutes: целевое время до встречи (15, 60 или 1440)
    tolerance: разброс в минутах (по умолчанию ±10 минут)
    """
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    sender = NotificationSender(bot)
    try:
        now = timezone.now()
        window_start = now + timedelta(minutes=window_minutes - tolerance)
        window_end = now + timedelta(minutes=window_minutes + tolerance)

        meetings = await sync_to_async(list)(
            Meeting.objects.filter(
                status="active",
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

                # ═══ Проверяем настройки пользователя ═══
                settings_obj = await sync_to_async(
                    lambda: UserNotificationSettings.objects.filter(
                        user=user, meeting_reminder_enabled=True
                    ).first()
                )()

                if not settings_obj:
                    # Нет настроек — используем значение по умолчанию (60 мин)
                    if window_minutes != 60:
                        continue
                elif settings_obj.meeting_reminder_minutes != window_minutes:
                    # У пользователя другое время — пропускаем
                    continue

                # Проверяем, не отправляли ли уже
                reminder_field = None
                if window_minutes == 1440:
                    reminder_field = "daily_reminder_sent"
                    already_sent = meeting.daily_reminder_sent
                else:
                    reminder_field = "reminder_sent"
                    already_sent = meeting.reminder_sent

                if already_sent:
                    continue

                # Отправляем с кнопками подтверждения
                keyboard = meeting_confirmation_keyboard(meeting.id)
                if window_minutes == 1440:
                    success = await sender.send_meeting_in_24_hours(user, meeting)
                else:
                    # Пробуем отправить с клавиатурой (если sender её поддерживает)
                    success = await sender.send_meeting_in_1_hour(user, meeting)

                if success:
                    sent_count += 1

            # Отмечаем отправленным только если хоть один получил
            if window_minutes == 1440:
                if not meeting.daily_reminder_sent and sent_count > 0:
                    meeting.daily_reminder_sent = True
                    await sync_to_async(meeting.save)(update_fields=["daily_reminder_sent"])
            else:
                if not meeting.reminder_sent and sent_count > 0:
                    meeting.reminder_sent = True
                    await sync_to_async(meeting.save)(update_fields=["reminder_sent"])

        logger.info(
            "Meeting reminders [%d min] total: %s",
            window_minutes, sent_count,
        )
        return sent_count
    finally:
        await bot.session.close()


# ── Старые функции больше не нужны — всё идёт через универсальную ──

async def _send_meeting_1h_reminders_async():
    return await _send_meeting_reminders_by_window_async(60, tolerance=10)

async def _send_meeting_24h_reminders_async():
    return await _send_meeting_reminders_by_window_async(1440, tolerance=120)  # ±2 часа

async def _send_meeting_15min_reminders_async():
    return await _send_meeting_reminders_by_window_async(15, tolerance=5)


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

                # ═══ Проверяем настройки пользователя ═══
                settings_obj = await sync_to_async(
                    lambda: UserNotificationSettings.objects.filter(
                        user=user, task_reminder_enabled=True
                    ).first()
                )()
                if settings_obj and not settings_obj.task_reminder_enabled:
                    continue
                if not settings_obj:
                    # Нет настроек — шлём по умолчанию
                    pass

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

                # ═══ Проверяем настройки пользователя ═══
                settings_obj = await sync_to_async(
                    lambda: UserNotificationSettings.objects.filter(
                        user=user, task_reminder_enabled=True
                    ).first()
                )()
                if settings_obj and not settings_obj.task_reminder_enabled:
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

        # Получаем пользователей с включённым дайджестом
        digest_users = await sync_to_async(list)(
            UserNotificationSettings.objects.filter(
                digest_enabled=True,
                digest_time__hour=now.hour,
            ).select_related("user")
        )

        # Если есть пользователи с кастомным временем — шлём им отдельно
        # (для остальных — стандартный дайджест через TaskAssignee)

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

            # Проверяем digest_enabled для пользователя
            user_digest = [du for du in digest_users if du.user_id == ta.user_id]
            if user_digest:
                # У этого пользователя кастомное время — пропускаем,
                # он получит дайджест в своё время
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

                # Проверяем digest_enabled для пользователя
                user_digest = [du for du in digest_users if du.user_id == user.id]
                if user_digest:
                    continue

                if await sender.send_meeting_today(user, meeting):
                    sent_count += 1

        logger.info("Daily digest sent: %s notifications", sent_count)
        return sent_count
    finally:
        await bot.session.close()


# ═════════════════════════════════════════════════════════════════════
#  Уведомление о назначении задачи (новый исполнитель)
# ═════════════════════════════════════════════════════════════════════

async def _send_task_assigned_notification_async(task_id: int):
    """Отправляет уведомление исполнителям о новой задаче."""
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        task = await sync_to_async(
            lambda: Task.objects.filter(id=task_id)
            .select_related("topic__chat")
            .prefetch_related("assignees__user")
            .first()
        )()
        if not task:
            logger.warning("Task %s not found for notification", task_id)
            return 0

        chat_title = ""
        try:
            chat_title = task.topic.chat.title or ""
        except Exception:
            pass
        source_block = f"\n📍 Чат: {chat_title}\n" if chat_title else "\n"

        due_str = _format_due_date_sync(task)

        sent_count = 0
        for ta in task.assignees.all():
            user = ta.user
            if _is_bot_user(user):
                continue

            # ═══ Проверяем настройки пользователя ═══
            settings_obj = await sync_to_async(
                lambda: UserNotificationSettings.objects.filter(
                    user=user, task_reminder_enabled=True
                ).first()
            )()
            if settings_obj and not settings_obj.task_reminder_enabled:
                continue

            try:
                await bot.send_message(
                    user.telegram_id,
                    f"📌 <b>Вам назначена задача:</b>\n"
                    f"<b>{task.title}</b>\n"
                    f"{due_str}"
                    f"{source_block}"
                    f"Используйте /tasks для просмотра всех задач.",
                    parse_mode="HTML",
                )
                sent_count += 1
            except Exception as e:
                logger.warning("Failed to notify user %s: %s", user, e)

        logger.info("Task assigned notifications sent: %s for task_id=%s", sent_count, task_id)
        return sent_count
    finally:
        await bot.session.close()


# ═════════════════════════════════════════════════════════════════════
#  Уведомление создателю о задаче без исполнителя
# ═════════════════════════════════════════════════════════════════════

async def _send_unassigned_task_notification_async(task_id: int):
    """Отправляет создателю уведомление, что у задачи нет исполнителя."""
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        task = await sync_to_async(
            lambda: Task.objects.filter(id=task_id)
            .select_related("creator", "topic__chat")
            .first()
        )()
        if not task:
            logger.warning("Task %s not found for unassigned notification", task_id)
            return 0

        creator = task.creator
        if not creator:
            logger.warning("Task %s has no creator, skipping notification", task_id)
            return 0

        assignee_count = await sync_to_async(lambda: task.assignees.count())()
        if assignee_count > 0:
            return 0

        chat_title = ""
        try:
            chat_title = task.topic.chat.title or ""
        except Exception:
            pass
        source_block = f"\n📍 Чат: {chat_title}\n" if chat_title else "\n"

        due_str = _format_due_date_sync(task)

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="👤 Назначить исполнителя",
                callback_data=f"task_edit_assignee:{task_id}",
            )]
        ])

        try:
            await bot.send_message(
                creator.telegram_id,
                f"📌 <b>Задача без исполнителя:</b>\n"
                f"<b>{task.title}</b>\n"
                f"{due_str}"
                f"{source_block}"
                f"У задачи нет исполнителя. Напишите @username того, "
                f"кто должен её выполнить.\n\n"
                f"<i>Пример: @ivanov</i>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            logger.info("Unassigned notification sent for task %s to %s", task_id, creator)
            return 1
        except Exception as e:
            logger.warning("Failed to notify creator %s: %s", creator, e)
            return 0
    finally:
        await bot.session.close()


def _format_due_date_sync(task) -> str:
    """Синхронная версия _format_due_date для использования в Celery."""
    from django.utils import timezone as tz
    if not task.due_date:
        return "без срока"
    dt = task.due_date
    if tz.is_aware(dt):
        dt = tz.localtime(dt)

    if task.status == "open" and task.due_date < tz.now():
        diff = tz.now() - task.due_date
        days = diff.days
        hours = diff.seconds // 3600
        if days > 0:
            return f"🚨 Просрочено на {days}д {hours}ч"
        else:
            return f"🚨 Просрочено на {hours}ч"

    return f"📅 до {dt.strftime('%d.%m.%Y')}"


# ═════════════════════════════════════════════════════════════════════
#  Уведомление о назначении встречи (новые участники)
# ═════════════════════════════════════════════════════════════════════

async def _send_meeting_assigned_notification_async(meeting_id: int):
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        meeting = await sync_to_async(
            lambda: Meeting.objects.filter(id=meeting_id)
            .select_related("topic__chat")
            .prefetch_related("participants")
            .first()
        )()
        if not meeting:
            logger.warning("Meeting %s not found for notification", meeting_id)
            return 0

        chat_title = ""
        try:
            chat_title = meeting.topic.chat.title or ""
        except Exception:
            pass
        source_block = f"\n📍 Чат: {chat_title}\n" if chat_title else "\n"

        dt = meeting.start_at
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        time_str = dt.strftime("%d.%m.%Y %H:%M")

        sent_count = 0
        for user in meeting.participants.all():
            if _is_bot_user(user):
                continue

            # ═══ Проверяем настройки пользователя ═══
            settings_obj = await sync_to_async(
                lambda: UserNotificationSettings.objects.filter(
                    user=user, meeting_reminder_enabled=True
                ).first()
            )()
            if settings_obj and not settings_obj.meeting_reminder_enabled:
                continue

            try:
                await bot.send_message(
                    user.telegram_id,
                    f"📅 <b>Вы приглашены на встречу:</b>\n"
                    f"<b>{meeting.title}</b>\n"
                    f"  ⏰ {time_str}"
                    f"{source_block}"
                    f"Используйте /meetings для просмотра всех встреч.",
                    parse_mode="HTML",
                )
                # ═══ Отправляем кнопки подтверждения ═══
                from bot.keyboards.inline import meeting_confirmation_keyboard
                await bot.send_message(
                    user.telegram_id,
                    f"Вы будете на встрече «{meeting.title}» ({time_str})?",
                    reply_markup=meeting_confirmation_keyboard(meeting.id),
                )
                sent_count += 1
            except Exception as e:
                logger.warning("Failed to notify user %s: %s", user, e)

        logger.info(
            "Meeting assigned notifications sent: %s for meeting_id=%s",
            sent_count, meeting_id,
        )
        return sent_count
    finally:
        await bot.session.close()


# ═════════════════════════════════════════════════════════════════════
#  Celery-задачи
# ═════════════════════════════════════════════════════════════════════


@shared_task(name="celery_app.tasks.send_reminders.send_meeting_reminders")
def send_meeting_reminders():
    """Напоминания за 1 час (или 15 минут — по настройкам пользователя)."""
    return _run_async(_send_meeting_1h_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_meeting_24h_reminders")
def send_meeting_24h_reminders():
    """Напоминания за 1 день."""
    return _run_async(_send_meeting_24h_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_meeting_15min_reminders")
def send_meeting_15min_reminders():
    """Напоминания за 15 минут."""
    return _run_async(_send_meeting_15min_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_task_24h_reminders")
def send_task_24h_reminders():
    return _run_async(_send_task_24h_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_overdue_task_reminders")
def send_overdue_task_reminders():
    return _run_async(_send_overdue_task_reminders_async())


@shared_task(name="celery_app.tasks.send_reminders.send_daily_digest")
def send_daily_digest():
    return _run_async(_send_daily_digest_async())


@shared_task(name="celery_app.tasks.send_reminders.send_task_assigned_notification")
def send_task_assigned_notification(task_id: int):
    return _run_async(_send_task_assigned_notification_async(task_id))


@shared_task(name="celery_app.tasks.send_reminders.send_unassigned_task_notification")
def send_unassigned_task_notification(task_id: int):
    return _run_async(_send_unassigned_task_notification_async(task_id))


@shared_task(name="celery_app.tasks.send_reminders.send_meeting_without_participants_notification")
def send_meeting_without_participants_notification(meeting_id: int):
    return _run_async(_send_meeting_without_participants_async(meeting_id))


@shared_task(name="celery_app.tasks.send_reminders.send_meeting_assigned_notification")
def send_meeting_assigned_notification(meeting_id: int):
    return _run_async(_send_meeting_assigned_notification_async(meeting_id))


@shared_task(name="celery_app.tasks.send_reminders.send_task_changed_notification")
def send_task_changed_notification(task_id: int, changes: str):
    return _run_async(_send_task_changed_notification_async(task_id, changes))


@shared_task(name="celery_app.tasks.send_reminders.send_meeting_changed_notification")
def send_meeting_changed_notification(meeting_id: int, changes: str):
    return _run_async(_send_meeting_changed_notification_async(meeting_id, changes))


@shared_task(name="celery_app.tasks.send_reminders.send_meeting_cancelled_notification")
def send_meeting_cancelled_notification(meeting_id: int):
    return _run_async(_send_meeting_cancelled_notification_async(meeting_id))


# ═════════════════════════════════════════════════════════════════════
#  Уведомление об изменении задачи (исполнителям)
# ═════════════════════════════════════════════════════════════════════

async def _send_task_changed_notification_async(task_id: int, changes: str):
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        task = await sync_to_async(
            lambda: Task.objects.filter(id=task_id)
            .select_related("topic__chat")
            .prefetch_related("assignees__user")
            .first()
        )()
        if not task:
            return 0

        chat_title = ""
        try:
            chat_title = task.topic.chat.title or ""
        except Exception:
            pass
        source_block = f"\n📍 Чат: {chat_title}\n" if chat_title else "\n"

        sent_count = 0
        for ta in task.assignees.all():
            user = ta.user
            if _is_bot_user(user):
                continue

            settings_obj = await sync_to_async(
                lambda: UserNotificationSettings.objects.filter(
                    user=user, task_reminder_enabled=True
                ).first()
            )()
            if settings_obj and not settings_obj.task_reminder_enabled:
                continue

            try:
                await bot.send_message(
                    user.telegram_id,
                    f"📌 <b>Задача изменена:</b>\n"
                    f"<b>{task.title}</b>"
                    f"{source_block}"
                    f"{changes}\n\n"
                    f"Используйте /tasks для просмотра.",
                    parse_mode="HTML",
                )
                sent_count += 1
            except Exception as e:
                logger.warning("Failed to notify user %s: %s", user, e)
        return sent_count
    finally:
        await bot.session.close()


# ═════════════════════════════════════════════════════════════════════
#  Уведомление об изменении встречи (участникам)
# ═════════════════════════════════════════════════════════════════════

async def _send_meeting_changed_notification_async(meeting_id: int, changes: str):
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        meeting = await sync_to_async(
            lambda: Meeting.objects.filter(id=meeting_id)
            .select_related("topic__chat")
            .prefetch_related("participants")
            .first()
        )()
        if not meeting:
            return 0

        chat_title = ""
        try:
            chat_title = meeting.topic.chat.title or ""
        except Exception:
            pass
        source_block = f"\n📍 Чат: {chat_title}\n" if chat_title else "\n"

        sent_count = 0
        for user in meeting.participants.all():
            if _is_bot_user(user):
                continue

            settings_obj = await sync_to_async(
                lambda: UserNotificationSettings.objects.filter(
                    user=user, meeting_reminder_enabled=True
                ).first()
            )()
            if settings_obj and not settings_obj.meeting_reminder_enabled:
                continue

            try:
                await bot.send_message(
                    user.telegram_id,
                    f"📅 <b>Встреча изменена:</b>\n"
                    f"<b>{meeting.title}</b>"
                    f"{source_block}"
                    f"{changes}\n\n"
                    f"Используйте /meetings для просмотра.",
                    parse_mode="HTML",
                )
                sent_count += 1
            except Exception as e:
                logger.warning("Failed to notify user %s: %s", user, e)
        return sent_count
    finally:
        await bot.session.close()


# ═════════════════════════════════════════════════════════════════════
#  Уведомление об отмене встречи
# ═════════════════════════════════════════════════════════════════════

async def _send_meeting_cancelled_notification_async(meeting_id: int):
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        meeting = await sync_to_async(
            lambda: Meeting.objects.filter(id=meeting_id)
            .select_related("topic__chat")
            .prefetch_related("participants")
            .first()
        )()
        if not meeting:
            return 0

        chat_title = ""
        try:
            chat_title = meeting.topic.chat.title or ""
        except Exception:
            pass
        source_block = f"\n📍 Чат: {chat_title}\n" if chat_title else "\n"

        dt = meeting.start_at
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        time_str = dt.strftime("%d.%m.%Y %H:%M")

        sent_count = 0
        for user in meeting.participants.all():
            if _is_bot_user(user):
                continue
            try:
                await bot.send_message(
                    user.telegram_id,
                    f"❌ <b>Встреча отменена:</b>\n"
                    f"<b>{meeting.title}</b>\n"
                    f"  ⏰ {time_str}"
                    f"{source_block}",
                    parse_mode="HTML",
                )
                sent_count += 1
            except Exception as e:
                logger.warning("Failed to notify user %s: %s", user, e)
        return sent_count
    finally:
        await bot.session.close()


# ═════════════════════════════════════════════════════════════════════
#  Уведомление создателю о встрече без участников
# ═════════════════════════════════════════════════════════════════════

async def _send_meeting_without_participants_async(meeting_id: int):
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        meeting = await sync_to_async(
            lambda: Meeting.objects.filter(id=meeting_id)
            .select_related("creator", "topic__chat")
            .first()
        )()
        if not meeting:
            return 0

        creator = meeting.creator
        if not creator:
            return 0

        participant_count = await sync_to_async(lambda: meeting.participants.count())()
        if participant_count > 0:
            return 0

        chat_title = ""
        try:
            chat_title = meeting.topic.chat.title or ""
        except Exception:
            pass
        source_block = f"\n📍 Чат: {chat_title}\n" if chat_title else "\n"

        dt = meeting.start_at
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        time_str = dt.strftime("%d.%m.%Y %H:%M")

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="👤 Назначить участников",
                callback_data=f"meeting_edit_participants:{meeting_id}",
            )]
        ])

        try:
            await bot.send_message(
                creator.telegram_id,
                f"📅 <b>Встреча без участников:</b>\n"
                f"<b>{meeting.title}</b>\n"
                f"  ⏰ {time_str}"
                f"{source_block}"
                f"У встречи нет участников. Напишите @username тех, "
                f"кого нужно пригласить.\n\n"
                f"<i>Пример: @ivanov @petrov</i>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return 1
        except Exception as e:
            logger.warning("Failed to notify creator %s: %s", creator, e)
            return 0
    finally:
        await bot.session.close()
