import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch


def _make_user(telegram_id=999, username="testuser", is_bot=False):
    u = MagicMock()
    u.telegram_id = telegram_id
    u.username = username
    u.full_name = "Test"
    u.is_bot = is_bot
    return u


def _make_bot_user():
    return _make_user(telegram_id=888, username="Neirix1_bot", is_bot=True)


def _make_task(due_date=None):
    t = MagicMock()
    t.id = 1
    t.title = "Test Task"
    t.status = "open"
    t.due_date = due_date
    t.topic = MagicMock()
    t.topic.chat = MagicMock(title="Chat")
    return t


def _make_task_assignee(task=None, user=None):
    ta = MagicMock()
    ta.task = task or _make_task()
    ta.user = user or _make_user()
    return ta


def _make_meeting(start_at=None, participants=None):
    m = MagicMock()
    m.id = 1
    m.title = "Test Meeting"
    m.status = "active"
    m.start_at = start_at or (datetime.now(dt_timezone.utc) + timedelta(hours=3))
    m.participants.all.return_value = participants or []
    m.topic = MagicMock()
    m.topic.chat = MagicMock(title="Chat")
    return m


def _mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.session = AsyncMock()
    return bot


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
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.TaskAssignee.objects") as mock_ta, \
             patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_m, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_ta.filter.return_value.select_related.return_value.order_by.return_value = [ta]
            mock_m.filter.return_value.prefetch_related.return_value.select_related.return_value.order_by.return_value = [meeting]

            result = await _send_daily_digest_async()

        assert result == 2

    @pytest.mark.asyncio
    async def test_skips_bots_in_tasks(self):
        from celery_app.tasks.send_reminders import _send_daily_digest_async

        bot_user = _make_bot_user()
        task = _make_task(due_date=datetime.now(dt_timezone.utc) + timedelta(hours=5))
        ta = _make_task_assignee(task=task, user=bot_user)
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.TaskAssignee.objects") as mock_ta, \
             patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_m, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_ta.filter.return_value.select_related.return_value.order_by.return_value = [ta]
            mock_m.filter.return_value.prefetch_related.return_value.select_related.return_value.order_by.return_value = []

            result = await _send_daily_digest_async()

        assert result == 0

    @pytest.mark.asyncio
    async def test_empty_day(self):
        from celery_app.tasks.send_reminders import _send_daily_digest_async

        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.TaskAssignee.objects") as mock_ta, \
             patch("celery_app.tasks.send_reminders.Meeting.objects") as mock_m, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_ta.filter.return_value.select_related.return_value.order_by.return_value = []
            mock_m.filter.return_value.prefetch_related.return_value.select_related.return_value.order_by.return_value = []

            result = await _send_daily_digest_async()

        assert result == 0