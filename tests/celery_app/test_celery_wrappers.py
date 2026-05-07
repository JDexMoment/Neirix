import pytest
from unittest.mock import AsyncMock, patch


class TestCeleryTaskWrappers:

    def test_send_meeting_reminders(self):
        from celery_app.tasks.send_reminders import send_meeting_reminders

        with patch(
            "celery_app.tasks.send_reminders._send_meeting_1h_reminders_async",
            new=AsyncMock(return_value=5),
        ):
            assert send_meeting_reminders() == 5

    def test_send_daily_digest(self):
        from celery_app.tasks.send_reminders import send_daily_digest

        with patch(
            "celery_app.tasks.send_reminders._send_daily_digest_async",
            new=AsyncMock(return_value=3),
        ):
            assert send_daily_digest() == 3

    def test_send_overdue(self):
        from celery_app.tasks.send_reminders import send_overdue_task_reminders

        with patch(
            "celery_app.tasks.send_reminders._send_overdue_task_reminders_async",
            new=AsyncMock(return_value=2),
        ):
            assert send_overdue_task_reminders() == 2

    def test_send_meeting_24h(self):
        from celery_app.tasks.send_reminders import send_meeting_24h_reminders

        with patch(
            "celery_app.tasks.send_reminders._send_meeting_24h_reminders_async",
            new=AsyncMock(return_value=4),
        ):
            assert send_meeting_24h_reminders() == 4

    def test_send_task_24h(self):
        from celery_app.tasks.send_reminders import send_task_24h_reminders

        with patch(
            "celery_app.tasks.send_reminders._send_task_24h_reminders_async",
            new=AsyncMock(return_value=1),
        ):
            assert send_task_24h_reminders() == 1