import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from tests.conftest import make_message


# ─────────────────────────────────────────────────────────────────────
# Хелперы для создания моков
# ─────────────────────────────────────────────────────────────────────


def _make_user(telegram_id=999, username="testuser", full_name="Test User", is_bot=False):
    user = MagicMock()
    user.telegram_id = telegram_id
    user.username = username
    user.full_name = full_name
    user.is_bot = is_bot
    return user


def _make_bot_user(telegram_id=888, username="Neirix1_bot"):
    return _make_user(telegram_id=telegram_id, username=username, is_bot=True)


def _make_task(task_id=1, title="Test Task", status="open", due_date=None):
    task = MagicMock()
    task.id = task_id
    task.title = title
    task.status = status
    task.due_date = due_date
    topic = MagicMock()
    topic.chat = MagicMock()
    topic.chat.title = "Test Chat"
    task.topic = topic
    return task


def _make_meeting(
    meeting_id=1,
    title="Test Meeting",
    status="active",
    start_at=None,
    reminder_sent=False,
    participants=None,
):
    meeting = MagicMock()
    meeting.id = meeting_id
    meeting.title = title
    meeting.status = status
    meeting.start_at = start_at or (datetime.now(dt_timezone.utc) + timedelta(hours=1))
    meeting.reminder_sent = reminder_sent
    meeting.save = MagicMock()
    meeting.participants.all.return_value = participants or []
    topic = MagicMock()
    topic.chat = MagicMock()
    topic.chat.title = "Test Chat"
    meeting.topic = topic
    return meeting


def _make_task_assignee(task=None, user=None):
    ta = MagicMock()
    ta.task = task or _make_task()
    ta.user = user or _make_user()
    return ta


# ─────────────────────────────────────────────────────────────────────
# Тесты: _is_bot_user
# ─────────────────────────────────────────────────────────────────────


class TestIsBotUser:

    def test_is_bot_flag(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        user = _make_user(is_bot=True, username="regular_name")
        assert _is_bot_user(user) is True

    def test_bot_username_suffix(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        user = _make_user(is_bot=False, username="Neirix1_bot")
        assert _is_bot_user(user) is True

    def test_bot_username_suffix_capital(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        user = _make_user(is_bot=False, username="MyBot")
        assert _is_bot_user(user) is True

    def test_regular_user(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        user = _make_user(is_bot=False, username="JDexMoment")
        assert _is_bot_user(user) is False

    def test_no_username(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        user = _make_user(is_bot=False, username=None)
        assert _is_bot_user(user) is False

    def test_username_contains_bot_not_at_end(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        user = _make_user(is_bot=False, username="botmaster")
        assert _is_bot_user(user) is False


# ─────────────────────────────────────────────────────────────────────
# Тесты: send_meeting_reminders (за час до встречи)
# ─────────────────────────────────────────────────────────────────────


class TestSendMeetingReminders:

    @pytest.mark.asyncio
    async def test_sends_to_participants(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        user1 = _make_user(telegram_id=111, username="user1")
        user2 = _make_user(telegram_id=222, username="user2")
        meeting = _make_meeting(participants=[user1, user2])

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            result = await _send_meeting_1h_reminders_async()

        assert result == 2
        assert mock_bot_instance.send_message.call_count == 2
        meeting.save.assert_called_once_with(update_fields=["reminder_sent"])

    @pytest.mark.asyncio
    async def test_skips_bot_participants(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        human = _make_user(telegram_id=111, username="human")
        bot_user = _make_bot_user()
        meeting = _make_meeting(participants=[human, bot_user])

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            result = await _send_meeting_1h_reminders_async()

        assert result == 1

    @pytest.mark.asyncio
    async def test_no_meetings(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = []

            result = await _send_meeting_1h_reminders_async()

        assert result == 0

    @pytest.mark.asyncio
    async def test_marks_reminder_sent(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        user = _make_user()
        meeting = _make_meeting(participants=[user])

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            await _send_meeting_1h_reminders_async()

        assert meeting.reminder_sent is True
        meeting.save.assert_called_once_with(update_fields=["reminder_sent"])

    @pytest.mark.asyncio
    async def test_send_failure_still_continues(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        user1 = _make_user(telegram_id=111)
        user2 = _make_user(telegram_id=222)
        meeting = _make_meeting(participants=[user1, user2])

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock(
                side_effect=[Exception("Network error"), None]
            )
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            result = await _send_meeting_1h_reminders_async()

        assert result == 1
        meeting.save.assert_called_once()

# ─────────────────────────────────────────────────────────────────────
# Тесты: send_daily_digest (утренний дайджест)
# ─────────────────────────────────────────────────────────────────────


class TestSendDailyDigest:

    @pytest.mark.asyncio
    async def test_sends_tasks_and_meetings(self):
        from celery_app.tasks.send_reminders import _send_daily_digest_async

        user = _make_user(telegram_id=111)
        task = _make_task(due_date=datetime.now(dt_timezone.utc) + timedelta(hours=5))
        ta = _make_task_assignee(task=task, user=user)
        meeting = _make_meeting(
            start_at=datetime.now(dt_timezone.utc) + timedelta(hours=3),
            participants=[user],
        )

        with patch("celery_app.tasks.send_reminders.TaskAssignee.objects") as mock_ta_qs, \
             patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_m_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_ta_qs.filter.return_value \
                .select_related.return_value \
                .order_by.return_value = [ta]

            mock_m_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value \
                .order_by.return_value = [meeting]

            result = await _send_daily_digest_async()

        # 1 task notification + 1 meeting notification
        assert result == 2
        assert mock_bot_instance.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_bots_in_tasks(self):
        from celery_app.tasks.send_reminders import _send_daily_digest_async

        bot_user = _make_bot_user()
        task = _make_task(due_date=datetime.now(dt_timezone.utc) + timedelta(hours=5))
        ta = _make_task_assignee(task=task, user=bot_user)

        with patch("celery_app.tasks.send_reminders.TaskAssignee.objects") as mock_ta_qs, \
             patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_m_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_ta_qs.filter.return_value \
                .select_related.return_value \
                .order_by.return_value = [ta]

            mock_m_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value \
                .order_by.return_value = []

            result = await _send_daily_digest_async()

        assert result == 0
        mock_bot_instance.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_day(self):
        from celery_app.tasks.send_reminders import _send_daily_digest_async

        with patch("celery_app.tasks.send_reminders.TaskAssignee.objects") as mock_ta_qs, \
             patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_m_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_ta_qs.filter.return_value \
                .select_related.return_value \
                .order_by.return_value = []

            mock_m_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value \
                .order_by.return_value = []

            result = await _send_daily_digest_async()

        assert result == 0


# ─────────────────────────────────────────────────────────────────────
# Тесты: send_overdue_task_reminders
# ─────────────────────────────────────────────────────────────────────


class TestSendOverdueTaskReminders:

    @pytest.mark.asyncio
    async def test_sends_overdue_notifications(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        user = _make_user(telegram_id=111)
        task = _make_task(
            title="Overdue Task",
            due_date=datetime.now(dt_timezone.utc) - timedelta(days=1),
        )
        ta = _make_task_assignee(task=task, user=user)
        # Мокаем task.assignees.all() чтобы вернуть assignee
        task.assignees.all.return_value = [ta]

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_task_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_task_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [task]

            result = await _send_overdue_task_reminders_async()

        assert result == 1

    @pytest.mark.asyncio
    async def test_skips_bots(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        bot_user = _make_bot_user()
        task = _make_task(due_date=datetime.now(dt_timezone.utc) - timedelta(days=1))
        ta = _make_task_assignee(task=task, user=bot_user)
        task.assignees.all.return_value = [ta]

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_task_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_task_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [task]

            result = await _send_overdue_task_reminders_async()

        assert result == 0

    @pytest.mark.asyncio
    async def test_no_overdue_tasks(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_task_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_task_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = []

            result = await _send_overdue_task_reminders_async()

        assert result == 0

    @pytest.mark.asyncio
    async def test_multiple_assignees(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        user1 = _make_user(telegram_id=111, username="user1")
        user2 = _make_user(telegram_id=222, username="user2")
        task = _make_task(due_date=datetime.now(dt_timezone.utc) - timedelta(hours=2))
        ta1 = _make_task_assignee(task=task, user=user1)
        ta2 = _make_task_assignee(task=task, user=user2)
        task.assignees.all.return_value = [ta1, ta2]

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_task_qs, \
             patch("celery_app.tasks.send_reminders.Bot") as MockBot:

            mock_bot_instance = AsyncMock()
            mock_bot_instance.send_message = AsyncMock()
            mock_bot_instance.session = AsyncMock()
            MockBot.return_value = mock_bot_instance

            mock_task_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [task]

            result = await _send_overdue_task_reminders_async()

        assert result == 2


# ─────────────────────────────────────────────────────────────────────
# Тесты: синхронные celery-обёртки
# ─────────────────────────────────────────────────────────────────────


class TestCeleryTaskWrappers:

    def test_send_meeting_reminders(self):
        from celery_app.tasks.send_reminders import send_meeting_reminders

        with patch(
            "celery_app.tasks.send_reminders._send_meeting_1h_reminders_async",
            new=AsyncMock(return_value=5),
        ):
            result = send_meeting_reminders()
            assert result == 5

    def test_send_daily_digest(self):
        from celery_app.tasks.send_reminders import send_daily_digest

        with patch(
            "celery_app.tasks.send_reminders._send_daily_digest_async",
            new=AsyncMock(return_value=3),
        ):
            result = send_daily_digest()
            assert result == 3

    def test_send_overdue(self):
        from celery_app.tasks.send_reminders import send_overdue_task_reminders

        with patch(
            "celery_app.tasks.send_reminders._send_overdue_task_reminders_async",
            new=AsyncMock(return_value=2),
        ):
            result = send_overdue_task_reminders()
            assert result == 2


# ─────────────────────────────────────────────────────────────────────
# Тесты: NotificationSender
# ─────────────────────────────────────────────────────────────────────


class TestNotificationSender:

    @pytest.mark.asyncio
    async def test_send_notification_success(self):
        from bot.services.notification_sender import NotificationSender

        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()
        sender = NotificationSender(mock_bot)

        user = _make_user()
        result = await sender.send_notification(user, "Test message")

        assert result is True
        mock_bot.send_message.assert_called_once_with(
            chat_id=user.telegram_id,
            text="Test message",
            parse_mode="HTML",
        )

    @pytest.mark.asyncio
    async def test_send_notification_user_blocked(self):
        from bot.services.notification_sender import NotificationSender
        from aiogram.exceptions import TelegramForbiddenError

        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(
            side_effect=TelegramForbiddenError(method=MagicMock(), message="Forbidden")
        )
        sender = NotificationSender(mock_bot)

        user = _make_user()
        result = await sender.send_notification(user, "Test")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_no_user(self):
        from bot.services.notification_sender import NotificationSender

        mock_bot = AsyncMock()
        sender = NotificationSender(mock_bot)

        result = await sender.send_notification(None, "Test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_no_telegram_id(self):
        from bot.services.notification_sender import NotificationSender

        mock_bot = AsyncMock()
        sender = NotificationSender(mock_bot)

        user = _make_user()
        user.telegram_id = None
        result = await sender.send_notification(user, "Test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_meeting_soon_text(self):
        from bot.services.notification_sender import NotificationSender

        bot = AsyncMock()
        sender = NotificationSender(bot)
        meeting = _make_meeting(title="Daily Standup")

        await sender.send_meeting_in_1_hour(_make_user(), meeting)

        text = bot.send_message.call_args.kwargs["text"]
        assert "через 1 час" in text
        assert "Daily Standup" in text

    @pytest.mark.asyncio
    async def test_send_task_overdue_text(self):
        from bot.services.notification_sender import NotificationSender

        bot = AsyncMock()
        sender = NotificationSender(bot)
        task = _make_task(
            title="Fix bug",
            due_date=datetime.now(dt_timezone.utc) - timedelta(days=1),
        )

        await sender.send_task_overdue(_make_user(), task)

        text = bot.send_message.call_args.kwargs["text"]
        assert "просрочена" in text.lower()
        assert "Fix bug" in text