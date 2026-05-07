import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch


def _make_user(telegram_id=999, username="testuser", full_name="Test", is_bot=False):
    u = MagicMock()
    u.telegram_id = telegram_id
    u.username = username
    u.full_name = full_name
    u.is_bot = is_bot
    return u


def _make_bot_user(telegram_id=888, username="Neirix1_bot"):
    return _make_user(telegram_id=telegram_id, username=username, is_bot=True)


def _make_meeting(participants=None, title="Test Meeting"):
    m = MagicMock()
    m.id = 1
    m.title = title
    m.status = "active"
    m.start_at = datetime.now(dt_timezone.utc) + timedelta(hours=1)
    m.reminder_sent = False
    m.save = MagicMock()
    m.participants.all.return_value = participants or []
    m.topic = MagicMock()
    m.topic.chat = MagicMock(title="Test Chat")
    return m


def _mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.session = AsyncMock()
    bot.session.close = AsyncMock()
    return bot


class TestSendMeetingReminders:

    @pytest.mark.asyncio
    async def test_sends_to_participants(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        user1 = _make_user(telegram_id=111, username="user1")
        user2 = _make_user(telegram_id=222, username="user2")
        meeting = _make_meeting(participants=[user1, user2])
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            result = await _send_meeting_1h_reminders_async()

        assert result == 2
        assert bot.send_message.call_count == 2
        meeting.save.assert_called_once_with(update_fields=["reminder_sent"])

    @pytest.mark.asyncio
    async def test_skips_bot_participants(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        human = _make_user(telegram_id=111, username="human")
        bot_user = _make_bot_user()
        meeting = _make_meeting(participants=[human, bot_user])
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            result = await _send_meeting_1h_reminders_async()

        assert result == 1

    @pytest.mark.asyncio
    async def test_no_meetings(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

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
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

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
        bot = _mock_bot()
        bot.send_message = AsyncMock(side_effect=[Exception("Network error"), None])

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            result = await _send_meeting_1h_reminders_async()

        assert result == 1
        meeting.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_bots_zero_sent(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        bots = [_make_user(telegram_id=i, username=f"bot{i}_bot", is_bot=True) for i in range(5)]
        meeting = _make_meeting(participants=bots)
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            result = await _send_meeting_1h_reminders_async()

        assert result == 0
        meeting.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_participants(self):
        from celery_app.tasks.send_reminders import _send_meeting_1h_reminders_async

        meeting = _make_meeting(participants=[])
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value \
                .prefetch_related.return_value \
                .select_related.return_value = [meeting]

            result = await _send_meeting_1h_reminders_async()

        assert result == 0