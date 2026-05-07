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


def _make_task_assignee(task=None, user=None):
    ta = MagicMock()
    ta.task = task
    ta.user = user or _make_user()
    return ta


def _make_task(title="Task", due_date=None, assignees=None):
    t = MagicMock()
    t.id = 1
    t.title = title
    t.status = "open"
    t.due_date = due_date
    t.overdue_reminder_sent = False
    t.save = MagicMock()
    t.topic = MagicMock()
    t.topic.chat = MagicMock(title="Chat")

    links = []
    for u in (assignees or []):
        link = MagicMock()
        link.user = u
        links.append(link)
    t.assignees.all.return_value = links
    return t


def _mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.session = AsyncMock()
    return bot


class TestSendOverdueTaskReminders:

    @pytest.mark.asyncio
    async def test_sends_overdue_notifications(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        user = _make_user(telegram_id=111)
        task = _make_task(
            title="Overdue",
            due_date=datetime.now(dt_timezone.utc) - timedelta(days=1),
            assignees=[user],
        )
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value.prefetch_related.return_value.select_related.return_value = [task]

            result = await _send_overdue_task_reminders_async()

        assert result == 1

    @pytest.mark.asyncio
    async def test_skips_bots(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        bot_user = _make_bot_user()
        task = _make_task(
            due_date=datetime.now(dt_timezone.utc) - timedelta(days=1),
            assignees=[bot_user],
        )
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value.prefetch_related.return_value.select_related.return_value = [task]

            result = await _send_overdue_task_reminders_async()

        assert result == 0

    @pytest.mark.asyncio
    async def test_no_overdue(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value.prefetch_related.return_value.select_related.return_value = []

            result = await _send_overdue_task_reminders_async()

        assert result == 0

    @pytest.mark.asyncio
    async def test_multiple_assignees(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        user1 = _make_user(telegram_id=111, username="user1")
        user2 = _make_user(telegram_id=222, username="user2")
        task = _make_task(
            due_date=datetime.now(dt_timezone.utc) - timedelta(hours=2),
            assignees=[user1, user2],
        )
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value.prefetch_related.return_value.select_related.return_value = [task]

            result = await _send_overdue_task_reminders_async()

        assert result == 2

    @pytest.mark.asyncio
    async def test_marks_overdue_flag(self):
        from celery_app.tasks.send_reminders import _send_overdue_task_reminders_async

        user = _make_user()
        task = _make_task(
            due_date=datetime.now(dt_timezone.utc) - timedelta(days=1),
            assignees=[user],
        )
        bot = _mock_bot()

        with patch("celery_app.tasks.send_reminders.Task.objects") as mock_qs, \
             patch("celery_app.tasks.send_reminders.Bot", return_value=bot):

            mock_qs.filter.return_value.prefetch_related.return_value.select_related.return_value = [task]

            await _send_overdue_task_reminders_async()

        assert task.overdue_reminder_sent is True
        task.save.assert_called_once_with(update_fields=["overdue_reminder_sent"])