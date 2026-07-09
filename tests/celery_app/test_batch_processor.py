import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from asgiref.sync import sync_to_async


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
                    # Исправление: LLMClient импортируется как from core.utils.llm_client import LLMClient
                    # поэтому пэтчить надо путь в модуле, где он реально определён
                    with patch("core.utils.llm_client.LLMClient") as MockLLM:
                        mock_llm = MagicMock()
                        mock_llm.extract_all_from_messages = AsyncMock(
                            return_value={"tasks": [], "meetings": []}
                        )
                        MockLLM.return_value = mock_llm

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

                        from core.services.batch_processor import BatchProcessor
                        processor = BatchProcessor()
                        result = await processor.process_batch(-100, 0, buffer_data)

        # При ошибке извлечения — 0 созданных сущностей, но сообщения помечены
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

                        # Мокаем _create_task
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


# ══════════════════════════════════════════════════════════════════
# FULL FLOW TESTS (через batch-обработку с замоканным LLM)
# ══════════════════════════════════════════════════════════════════

class TestFullFlowTopicResolution:
    """
    Интеграционные тесты: BatchProcessor → TaskService / MeetingService
    с real-БД, но замоканным LLM.
    """

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.parametrize("entity_type", ["task", "meeting"])
    async def test_batch_from_private_creates_entity_with_group_topic(
        self, entity_type,
    ):
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.services.batch_processor import BatchProcessor
        from core.models import (
            TelegramChat, TelegramUser, Topic, Message, UserRole,
            Task, Meeting,
        )

        group_chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-4001, title="Dev Team", type="supergroup",
        )
        group_topic = await sync_to_async(Topic.objects.create)(
            chat=group_chat, thread_id=0,
        )
        author = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=4001, username="dev", full_name="Dev User",
        )
        await sync_to_async(UserRole.objects.create)(
            user=author, chat=group_chat, role="member",
        )
        private_chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=400100, title="", type="private",
        )
        private_topic = await sync_to_async(Topic.objects.create)(
            chat=private_chat, thread_id=0,
        )
        db_msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=701, chat=private_chat, topic=private_topic,
            author=author, text="задача @dev и встреча завтра в 14",
            timestamp=timezone.now(),
        )
        buffer_data = [{
            "message_id": db_msg.id,
            "text": db_msg.text,
            "author_name": author.full_name,
            "timestamp": db_msg.timestamp.timestamp(),
        }]

        if entity_type == "task":
            llm_return = {
                "tasks": [{"title": "отчёт", "assignees": [],
                           "due_date": None, "description": ""}],
                "meetings": [],
            }
        else:
            llm_return = {
                "tasks": [],
                "meetings": [{
                    "title": "встреча", "participants": [],
                    "date": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d"),
                    "time": "14:00", "description": "",
                }],
            }

        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.extract_all_from_messages = AsyncMock(return_value=llm_return)
            MockLLM.return_value = mock_llm
            with patch("core.services.batch_processor.generate_embeddings_batch",
                       new_callable=AsyncMock, return_value=[None]):
                with patch("core.services.batch_processor.VectorStoreClient"):
                    processor = BatchProcessor()
                    await processor.process_batch(
                        chat_id=private_chat.chat_id,
                        topic_id=private_topic.thread_id,
                        messages=buffer_data,
                    )

        # При проверках сравниваем по _id (FK) — безопасно из async-контекста
        if entity_type == "task":
            created = await sync_to_async(
                lambda: list(Task.objects.filter(creator=author))
            )()
            assert len(created) == 1
            task = created[0]
            assert task.topic_id == group_topic.pk, (
                f"Task topic_id={task.topic_id}, expected {group_topic.pk}"
            )
        else:
            created = await sync_to_async(
                lambda: list(Meeting.objects.filter(creator=author))
            )()
            assert len(created) == 1
            meeting = created[0]
            assert meeting.topic_id == group_topic.pk, (
                f"Meeting topic_id={meeting.topic_id}, expected {group_topic.pk}"
            )

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_batch_from_group_preserves_own_topic(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.services.batch_processor import BatchProcessor
        from core.models import TelegramChat, TelegramUser, Topic, Message, Task

        group_chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-5001, title="Team Chat", type="supergroup",
        )
        group_topic = await sync_to_async(Topic.objects.create)(
            chat=group_chat, thread_id=0,
        )
        author = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=5001, username="teammate", full_name="Teammate",
        )
        db_msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=801, chat=group_chat, topic=group_topic,
            author=author, text="нужно сделать отчёт",
            timestamp=timezone.now(),
        )
        buffer_data = [{
            "message_id": db_msg.id,
            "text": db_msg.text,
            "author_name": author.full_name,
            "timestamp": db_msg.timestamp.timestamp(),
        }]

        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.extract_all_from_messages = AsyncMock(return_value={
                "tasks": [{"title": "отчёт", "assignees": [],
                           "due_date": None, "description": ""}],
                "meetings": [],
            })
            MockLLM.return_value = mock_llm
            with patch("core.services.batch_processor.generate_embeddings_batch",
                       new_callable=AsyncMock, return_value=[None]):
                with patch("core.services.batch_processor.VectorStoreClient"):
                    processor = BatchProcessor()
                    await processor.process_batch(
                        chat_id=group_chat.chat_id,
                        topic_id=group_topic.thread_id,
                        messages=buffer_data,
                    )

        tasks = await sync_to_async(
            lambda: list(Task.objects.filter(creator=author))
        )()
        assert len(tasks) == 1
        # Сравниваем по _id — безопасно для async-контекста
        assert tasks[0].topic_id == group_topic.pk

