import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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

# ─────────────────────────────────────────────────────────────────────────────
# BATCH BUFFER PROCESSING TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessTargetBuffer:
    """Тесты для event-driven Celery-задачи process_target_buffer."""

    @pytest.mark.asyncio
    async def test_empty_buffer_returns_skipped(self):
        """
        Если буфер уже пуст, задача должна безопасно завершиться.
        Это покрывает сценарий, когда delayed task сработала позже,
        а буфер уже обработан немедленной task.
        """
        from celery_app.tasks.process_messages import _process_target_buffer_async

        with patch("core.services.message_buffer.MessageBuffer") as MockBuffer:
            mock_buffer = MagicMock()
            mock_buffer.flush.return_value = []
            MockBuffer.return_value = mock_buffer

            with patch("core.services.batch_processor.BatchProcessor") as MockProcessor:
                result = await _process_target_buffer_async(-100, 0)

        assert result == {"tasks": 0, "meetings": 0, "status": "skipped"}
        MockProcessor.assert_not_called()

    @pytest.mark.asyncio
    async def test_buffer_flushed_and_processed(self):
        """
        Если в буфере есть сообщения, они должны быть переданы в BatchProcessor.
        """
        from celery_app.tasks.process_messages import _process_target_buffer_async

        fake_messages = [
            {
                "message_id": 1,
                "text": "завтра встреча",
                "author_name": "boss",
                "timestamp": 1700000000.0,
            },
            {
                "message_id": 2,
                "text": "с директором",
                "author_name": "boss",
                "timestamp": 1700000001.0,
            },
        ]

        with patch("core.services.message_buffer.MessageBuffer") as MockBuffer:
            mock_buffer = MagicMock()
            mock_buffer.flush.return_value = fake_messages
            MockBuffer.return_value = mock_buffer

            with patch("core.services.batch_processor.BatchProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_batch = AsyncMock(
                    return_value={"tasks_created": 0, "meetings_created": 1}
                )
                MockProcessor.return_value = mock_processor

                result = await _process_target_buffer_async(-100, 0)

        assert result == {"tasks_created": 0, "meetings_created": 1}
        mock_buffer.flush.assert_called_once_with(-100, 0)
        mock_processor.process_batch.assert_called_once_with(-100, 0, fake_messages)

    def test_process_target_buffer_wrapper_calls_run_async(self):
        """
        Синхронная Celery task должна делегировать выполнение через _run_async.
        """
        from celery_app.tasks.process_messages import process_target_buffer

        with patch(
            "celery_app.tasks.process_messages._run_async",
            return_value={"tasks_created": 1, "meetings_created": 0},
        ) as mock_run_async:
            result = process_target_buffer(-100, 5)

        assert result == {"tasks_created": 1, "meetings_created": 0}
        mock_run_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_processor_exception_bubbles_up(self):
        """
        Если BatchProcessor падает, исключение не должно замалчиваться.
        Это полезно, чтобы Celery корректно помечал task как failed.
        """
        from celery_app.tasks.process_messages import _process_target_buffer_async

        fake_messages = [
            {
                "message_id": 1,
                "text": "test",
                "author_name": "user",
                "timestamp": 1700000000.0,
            }
        ]

        with patch("core.services.message_buffer.MessageBuffer") as MockBuffer:
            mock_buffer = MagicMock()
            mock_buffer.flush.return_value = fake_messages
            MockBuffer.return_value = mock_buffer

            with patch("core.services.batch_processor.BatchProcessor") as MockProcessor:
                mock_processor = MagicMock()
                mock_processor.process_batch = AsyncMock(
                    side_effect=Exception("processor failed")
                )
                MockProcessor.return_value = mock_processor

                with pytest.raises(Exception, match="processor failed"):
                    await _process_target_buffer_async(-100, 0)