import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from django.utils import timezone


@pytest.fixture
def mock_db_messages():
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
        msg.chat.type = "supergroup"
        msg.topic = MagicMock()
        msg.topic.thread_id = 0
        messages.append(msg)
    return messages


def _make_mock_qs(messages_list):
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
            with patch("core.services.batch_processor.user_can_create", return_value=True):
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
                with patch("core.services.batch_processor.user_can_create", return_value=True):
                    from core.services.batch_processor import BatchProcessor
                    processor = BatchProcessor()
                    result = await processor.process_batch(
                        -100, 0,
                        [{"message_id": 999, "text": "x", "author_name": "u", "timestamp": 1.0}],
                    )
        assert result == {"tasks_created": 0, "meetings_created": 0}

    @pytest.mark.asyncio
    async def test_embeddings_generated(self, mock_db_messages):
        """Embeddings генерируются и upsert'ятся."""
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
                    with patch("core.utils.llm_client.LLMClient") as MockLLM:
                        mock_llm = MagicMock()
                        mock_llm.extract_all_from_messages = AsyncMock(
                            return_value={"tasks": [], "meetings": []}
                        )
                        MockLLM.return_value = mock_llm

                        with patch("core.services.batch_processor.user_can_create", return_value=True):
                            from core.services.batch_processor import BatchProcessor
                            processor = BatchProcessor()
                            await processor.process_batch(-100, 0, buffer_data)

        mock_embed.assert_called_once()
        assert len(mock_embed.call_args[0][0]) == 3
        assert mock_vc.upsert_message.call_count == 3

    @pytest.mark.asyncio
    async def test_messages_marked_processed(self, mock_db_messages):
        """После обработки сообщения помечаются is_processed=True."""
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
                    with patch("core.utils.llm_client.LLMClient") as MockLLM:
                        mock_llm = MagicMock()
                        mock_llm.extract_all_from_messages = AsyncMock(
                            return_value={"tasks": [], "meetings": []}
                        )
                        MockLLM.return_value = mock_llm

                        with patch("core.services.batch_processor.user_can_create", return_value=True):
                            from core.services.batch_processor import BatchProcessor
                            processor = BatchProcessor()
                            await processor.process_batch(-100, 0, buffer_data)

        mock_qs.update.assert_called_once_with(is_processed=True)

    @pytest.mark.asyncio
    async def test_extraction_error_does_not_crash(self, mock_db_messages):
        """Ошибка при извлечении → не крашит batch."""
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
                    with patch("core.utils.llm_client.LLMClient") as MockLLM:
                        mock_llm = MagicMock()
                        mock_llm.extract_all_from_messages = AsyncMock(
                            side_effect=Exception("LLM error")
                        )
                        MockLLM.return_value = mock_llm

                        with patch("core.services.batch_processor.user_can_create", return_value=True):
                            from core.services.batch_processor import BatchProcessor
                            processor = BatchProcessor()
                            result = await processor.process_batch(-100, 0, buffer_data)

        assert result["tasks_created"] == 0
        assert result["meetings_created"] == 0

    @pytest.mark.asyncio
    async def test_tasks_created_counted(self, mock_db_messages):
        """LLM вернул 2 задачи → tasks_created=2."""
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
                    with patch("core.utils.llm_client.LLMClient") as MockLLM:
                        mock_llm = MagicMock()
                        mock_llm.extract_all_from_messages = AsyncMock(
                            return_value={
                                "tasks": [
                                    {"title": "Task 1", "assignees": [], "due_date": None, "description": ""},
                                    {"title": "Task 2", "assignees": [], "due_date": None, "description": ""},
                                ],
                                "meetings": [],
                            }
                        )
                        MockLLM.return_value = mock_llm

                        with patch("core.services.batch_processor.user_can_create", return_value=True):
                            with patch.object(
                                __import__("core.services.batch_processor", fromlist=["BatchProcessor"]).BatchProcessor,
                                "_create_task",
                                new_callable=AsyncMock,
                                side_effect=lambda data, msg: MagicMock(id=1),
                            ):
                                from core.services.batch_processor import BatchProcessor
                                processor = BatchProcessor()
                                result = await processor.process_batch(-100, 0, buffer_data)

        assert result["tasks_created"] == 2
        assert result["meetings_created"] == 0
