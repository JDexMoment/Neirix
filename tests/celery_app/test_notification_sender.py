import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock


def _make_user(telegram_id=999, username="testuser"):
    u = MagicMock()
    u.telegram_id = telegram_id
    u.username = username
    u.full_name = "Test"
    u.is_bot = False
    return u


def _make_meeting(title="Meeting"):
    m = MagicMock()
    m.id = 1
    m.title = title
    m.start_at = datetime.now(dt_timezone.utc) + timedelta(hours=1)
    m.participants.all.return_value = []
    return m


def _make_task(title="Task", due_date=None):
    t = MagicMock()
    t.id = 1
    t.title = title
    t.due_date = due_date
    return t


def _mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


class TestNotificationSender:

    @pytest.mark.asyncio
    async def test_send_success(self):
        from bot.services.notification_sender import NotificationSender

        bot = _mock_bot()
        sender = NotificationSender(bot)
        user = _make_user()

        result = await sender.send_notification(user, "Test")

        assert result is True
        bot.send_message.assert_called_once_with(
            chat_id=user.telegram_id,
            text="Test",
            parse_mode="HTML",
        )

    @pytest.mark.asyncio
    async def test_send_user_blocked(self):
        from bot.services.notification_sender import NotificationSender
        from aiogram.exceptions import TelegramForbiddenError

        bot = _mock_bot()
        bot.send_message = AsyncMock(
            side_effect=TelegramForbiddenError(method=MagicMock(), message="Forbidden")
        )
        sender = NotificationSender(bot)

        result = await sender.send_notification(_make_user(), "Test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_no_user(self):
        from bot.services.notification_sender import NotificationSender

        sender = NotificationSender(_mock_bot())
        assert await sender.send_notification(None, "Test") is False

    @pytest.mark.asyncio
    async def test_send_no_telegram_id(self):
        from bot.services.notification_sender import NotificationSender

        sender = NotificationSender(_mock_bot())
        user = _make_user()
        user.telegram_id = None
        assert await sender.send_notification(user, "Test") is False

    @pytest.mark.asyncio
    async def test_send_telegram_id_zero(self):
        from bot.services.notification_sender import NotificationSender

        sender = NotificationSender(_mock_bot())
        user = _make_user()
        user.telegram_id = 0
        result = await sender.send_notification(user, "Test")
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_meeting_1h_text(self):
        from bot.services.notification_sender import NotificationSender

        bot = _mock_bot()
        sender = NotificationSender(bot)

        await sender.send_meeting_in_1_hour(_make_user(), _make_meeting("Daily"))

        text = bot.send_message.call_args.kwargs["text"]
        assert "через 1 час" in text
        assert "Daily" in text

    @pytest.mark.asyncio
    async def test_meeting_24h_text(self):
        from bot.services.notification_sender import NotificationSender

        bot = _mock_bot()
        sender = NotificationSender(bot)

        await sender.send_meeting_in_24_hours(_make_user(), _make_meeting("Sprint"))

        text = bot.send_message.call_args.kwargs["text"]
        assert "завтра" in text.lower()
        assert "Sprint" in text

    @pytest.mark.asyncio
    async def test_task_overdue_text(self):
        from bot.services.notification_sender import NotificationSender

        bot = _mock_bot()
        sender = NotificationSender(bot)
        task = _make_task("Fix bug", due_date=datetime.now(dt_timezone.utc) - timedelta(days=1))

        await sender.send_task_overdue(_make_user(), task)

        text = bot.send_message.call_args.kwargs["text"]
        assert "просрочена" in text.lower()
        assert "Fix bug" in text

    @pytest.mark.asyncio
    async def test_task_24h_text(self):
        from bot.services.notification_sender import NotificationSender

        bot = _mock_bot()
        sender = NotificationSender(bot)
        task = _make_task("Deploy", due_date=datetime.now(dt_timezone.utc) + timedelta(hours=23))

        await sender.send_task_in_24_hours(_make_user(), task)

        text = bot.send_message.call_args.kwargs["text"]
        assert "завтра" in text.lower()
        assert "Deploy" in text

    @pytest.mark.asyncio
    async def test_meeting_cancelled_text(self):
        from bot.services.notification_sender import NotificationSender

        bot = _mock_bot()
        sender = NotificationSender(bot)

        await sender.send_meeting_cancelled(_make_user(), _make_meeting("Cancelled"))

        text = bot.send_message.call_args.kwargs["text"]
        assert "отменена" in text.lower()
        assert "Cancelled" in text

    @pytest.mark.asyncio
    async def test_meeting_rescheduled_text(self):
        from bot.services.notification_sender import NotificationSender

        bot = _mock_bot()
        sender = NotificationSender(bot)
        meeting = _make_meeting("Moved")
        old_time = datetime.now(dt_timezone.utc)

        await sender.send_meeting_rescheduled(_make_user(), meeting, old_time)

        text = bot.send_message.call_args.kwargs["text"]
        assert "перенесена" in text.lower()
        assert "Moved" in text