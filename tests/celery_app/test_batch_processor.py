"""
Тесты для BatchProcessor.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from django.utils import timezone


@pytest.fixture
def mock_db_messages():
    """Список мок-сообщений из БД."""
    messages = []
    for i in range(3):
        msg = MagicMock()
        msg.id = i + 1
        msg.text = f"test message {i + 1}"
        msg.timestamp = timezone.now()
        msg.is_processed = False

        msg.author = MagicMock()
        msg.author.username = f"user{i}"
        msg.author.full_name = f"User {i}"
        msg.author.telegram_id = 1000 + i

        msg.chat = MagicMock()
        msg.chat.chat_id = -100

        msg.topic = MagicMock()
        msg.topic.thread_id = 0

        messages.append(msg)
    return messages


def _make_mock_qs(messages_list):
    """Создаёт мок QuerySet, который ведёт себя как list()."""
    qs = MagicMock()
    qs.filter.return_value = qs
    qs.select_related.return_value = qs
    qs.order_by.return_value = qs
    qs.__iter__ = MagicMock(return_value=iter(messages_list))
    qs.__len__ = MagicMock(return_value=len(messages_list))
    qs.__bool__ = MagicMock(return_value=bool(messages_list))
    qs.update = MagicMock()
    return qs


class TestBatchProcessor:

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Пустой список → нули."""
        with patch("core.services.batch_processor.VectorStoreClient"):
            from core.services.batch_processor import BatchProcessor

            processor = BatchProcessor()
            result = await processor.process_batch(-100, 0, [])

        assert result == {"tasks_created": 0, "meetings_created": 0}

    @pytest.mark.asyncio
    async def test_db_messages_not_found(self):
        """Сообщения не найдены в БД → нули."""
        with patch("core.services.batch_processor.VectorStoreClient"):
            with patch("core.services.batch_processor.Message") as MockMsg:
                MockMsg.objects = _make_mock_qs([])

                from core.services.batch_processor import BatchProcessor

                processor = BatchProcessor()
                result = await processor.process_batch(
                    -100,
                    0,
                    [{"message_id": 999, "text": "x", "author_name": "u", "timestamp": 1.0}],
                )

        assert result == {"tasks_created": 0, "meetings_created": 0}

    @pytest.mark.asyncio
    async def test_embeddings_generated(self, mock_db_messages):
        """Embeddings генерируются и upsert'ятся для каждого сообщения."""
        buffer_data = [
            {"message_id": m.id, "text": m.text, "author_name": "u", "timestamp": 1.0}
            for m in mock_db_messages
        ]

        with patch("core.services.batch_processor.VectorStoreClient") as MockVC:
            mock_vc = MagicMock()
            MockVC.return_value = mock_vc

            with patch("core.services.batch_processor.Message") as MockMsg:
                MockMsg.objects = _make_mock_qs(mock_db_messages)

                with patch(
                    "core.services.batch_processor.generate_embeddings_batch",
                    new_callable=AsyncMock,
                    return_value=[[0.1, 0.2]] * 3,
                ) as mock_embed:
                    with patch("core.services.batch_processor.TaskService") as MockTS:
                        mock_ts = MagicMock()
                        mock_ts.extract_tasks_from_messages_batch = AsyncMock(
                            return_value=[]
                        )
                        MockTS.return_value = mock_ts

                        with patch(
                            "core.services.batch_processor.MeetingService"
                        ) as MockMS:
                            mock_ms = MagicMock()
                            mock_ms.extract_meetings_from_messages_batch = AsyncMock(
                                return_value=[]
                            )
                            MockMS.return_value = mock_ms

                            from core.services.batch_processor import BatchProcessor

                            processor = BatchProcessor()
                            await processor.process_batch(-100, 0, buffer_data)

        mock_embed.assert_called_once()
        assert len(mock_embed.call_args[0][0]) == 3
        assert mock_vc.upsert_message.call_count == 3

    @pytest.mark.asyncio
    async def test_messages_marked_processed(self, mock_db_messages):
        """После обработки батча сообщения помечаются is_processed=True."""
        buffer_data = [
            {"message_id": m.id, "text": m.text, "author_name": "u", "timestamp": 1.0}
            for m in mock_db_messages
        ]

        mock_qs = _make_mock_qs(mock_db_messages)

        with patch("core.services.batch_processor.VectorStoreClient"):
            with patch("core.services.batch_processor.Message") as MockMsg:
                MockMsg.objects = mock_qs

                with patch(
                    "core.services.batch_processor.generate_embeddings_batch",
                    new_callable=AsyncMock,
                    return_value=[None] * 3,
                ):
                    with patch("core.services.batch_processor.TaskService") as MockTS:
                        mock_ts = MagicMock()
                        mock_ts.extract_tasks_from_messages_batch = AsyncMock(
                            return_value=[]
                        )
                        MockTS.return_value = mock_ts

                        with patch(
                            "core.services.batch_processor.MeetingService"
                        ) as MockMS:
                            mock_ms = MagicMock()
                            mock_ms.extract_meetings_from_messages_batch = AsyncMock(
                                return_value=[]
                            )
                            MockMS.return_value = mock_ms

                            from core.services.batch_processor import BatchProcessor

                            processor = BatchProcessor()
                            await processor.process_batch(-100, 0, buffer_data)

        mock_qs.update.assert_called_once_with(is_processed=True)

    @pytest.mark.asyncio
    async def test_task_extraction_error_does_not_crash(self, mock_db_messages):
        """Ошибка при извлечении задач → не крашит весь batch."""
        buffer_data = [
            {"message_id": m.id, "text": m.text, "author_name": "u", "timestamp": 1.0}
            for m in mock_db_messages
        ]

        with patch("core.services.batch_processor.VectorStoreClient"):
            with patch("core.services.batch_processor.Message") as MockMsg:
                MockMsg.objects = _make_mock_qs(mock_db_messages)

                with patch(
                    "core.services.batch_processor.generate_embeddings_batch",
                    new_callable=AsyncMock,
                    return_value=[None] * 3,
                ):
                    with patch("core.services.batch_processor.TaskService") as MockTS:
                        mock_ts = MagicMock()
                        mock_ts.extract_tasks_from_messages_batch = AsyncMock(
                            side_effect=Exception("LLM exploded")
                        )
                        MockTS.return_value = mock_ts

                        with patch(
                            "core.services.batch_processor.MeetingService"
                        ) as MockMS:
                            mock_ms = MagicMock()
                            mock_ms.extract_meetings_from_messages_batch = AsyncMock(
                                return_value=[MagicMock()]
                            )
                            MockMS.return_value = mock_ms

                            from core.services.batch_processor import BatchProcessor

                            processor = BatchProcessor()
                            result = await processor.process_batch(
                                -100, 0, buffer_data
                            )

        assert result["tasks_created"] == 0
        assert result["meetings_created"] == 1