"""
Batch-тесты для задач — мокаем user_can_create в task_service.
"""
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from django.utils import timezone


@pytest.fixture
def mock_db_message():
    def _make(msg_id=1, text="", author_username="testuser", telegram_id=12345, timestamp=None):
        if timestamp is None:
            timestamp = timezone.now()
        msg = MagicMock()
        msg.id = msg_id
        msg.pk = msg_id
        msg.text = text
        msg.timestamp = timestamp
        msg.is_processed = False
        msg.save = MagicMock()
        msg.author = MagicMock()
        msg.author.username = author_username
        msg.author.full_name = author_username
        msg.author.telegram_id = telegram_id
        msg.author.is_bot = False
        msg.author.id = telegram_id
        msg.chat = MagicMock()
        msg.chat.chat_id = -100123
        msg.chat.type = "supergroup"
        msg.chat.id = -100123
        msg.topic = MagicMock()
        msg.topic.thread_id = 0
        return msg
    return _make


@pytest.mark.asyncio
async def test_batch_empty_messages():
    from core.services.task_service import TaskService
    service = TaskService(llm=MagicMock())
    result = await service.extract_tasks_from_messages_batch([])
    assert result == []


@pytest.mark.asyncio
async def test_batch_single_message_with_task(mock_db_message):
    from core.services.task_service import TaskService

    msg = mock_db_message(msg_id=1, text="сделай отчёт", author_username="boss")
    mock_llm = AsyncMock()
    mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[{
        "title": "сделать отчёт", "assignees": [], "due_date": None, "description": "",
    }])

    with patch("core.services.task_service.user_can_create", return_value=True):
        with patch("core.services.task_service.Task") as MockTask:
            MockTask.objects.create = MagicMock(return_value=MagicMock(id=1))
            service = TaskService(llm=mock_llm)
            result = await service.extract_tasks_from_messages_batch([msg])

    assert len(result) == 1


@pytest.mark.asyncio
async def test_batch_multiple_messages_combined_context(mock_db_message):
    from core.services.task_service import TaskService

    now = timezone.now()
    msgs = [
        mock_db_message(msg_id=1, text="нужно сделать отчёт", author_username="boss",
                        timestamp=now - timedelta(minutes=2)),
        mock_db_message(msg_id=2, text="@worker1 возьми", author_username="boss",
                        timestamp=now - timedelta(minutes=1)),
        mock_db_message(msg_id=3, text="ок", author_username="worker1", timestamp=now),
    ]
    mock_llm = AsyncMock()
    mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[{
        "title": "сделать отчёт", "assignees": [], "due_date": None, "description": "",
    }])

    with patch("core.services.task_service.user_can_create", return_value=True):
        with patch("core.services.task_service.Task") as MockTask:
            MockTask.objects.create = MagicMock(return_value=MagicMock(id=1))
            service = TaskService(llm=mock_llm)
            result = await service.extract_tasks_from_messages_batch(msgs)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_batch_no_tasks_found(mock_db_message):
    from core.services.task_service import TaskService
    msg = mock_db_message(msg_id=1, text="привет", author_username="user1")
    mock_llm = AsyncMock()
    mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[])
    service = TaskService(llm=mock_llm)
    result = await service.extract_tasks_from_messages_batch([msg])
    assert result == []


@pytest.mark.asyncio
async def test_batch_llm_error_returns_empty(mock_db_message):
    from core.services.task_service import TaskService
    msg = mock_db_message(msg_id=1, text="сделай отчёт", author_username="boss")
    mock_llm = AsyncMock()
    mock_llm.extract_tasks_from_messages = AsyncMock(side_effect=Exception("API error"))
    service = TaskService(llm=mock_llm)
    result = await service.extract_tasks_from_messages_batch([msg])
    assert result == []


@pytest.mark.asyncio
async def test_batch_multiple_tasks_from_batch(mock_db_message):
    from core.services.task_service import TaskService

    now = timezone.now()
    msgs = [
        mock_db_message(msg_id=1, text="задача 1", author_username="boss",
                        timestamp=now - timedelta(minutes=1)),
        mock_db_message(msg_id=2, text="задача 2", author_username="boss", timestamp=now),
    ]
    mock_llm = AsyncMock()
    mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[
        {"title": "задача 1", "assignees": [], "due_date": None, "description": ""},
        {"title": "задача 2", "assignees": [], "due_date": None, "description": ""},
    ])
    counter = {"count": 0}

    def make_task(**kwargs):
        counter["count"] += 1
        t = MagicMock()
        t.id = counter["count"]
        return t

    with patch("core.services.task_service.user_can_create", return_value=True):
        with patch("core.services.task_service.Task") as MockTask:
            MockTask.objects.create = MagicMock(side_effect=make_task)
            service = TaskService(llm=mock_llm)
            result = await service.extract_tasks_from_messages_batch(msgs)

    assert len(result) == 2


@pytest.mark.asyncio
async def test_batch_source_message_is_last(mock_db_message):
    from core.services.task_service import TaskService

    now = timezone.now()
    msgs = [
        mock_db_message(msg_id=1, text="нужно сделать отчёт", author_username="boss",
                        timestamp=now - timedelta(minutes=1)),
        mock_db_message(msg_id=2, text="@worker1 возьми", author_username="boss", timestamp=now),
    ]
    mock_llm = AsyncMock()
    mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[{
        "title": "сделать отчёт", "assignees": [], "due_date": None, "description": "",
    }])
    created_kwargs = {}

    def capture_create(**kwargs):
        created_kwargs.update(kwargs)
        return MagicMock(id=1)

    with patch("core.services.task_service.user_can_create", return_value=True):
        with patch("core.services.task_service.Task") as MockTask:
            MockTask.objects.create = MagicMock(side_effect=capture_create)
            service = TaskService(llm=mock_llm)
            await service.extract_tasks_from_messages_batch(msgs)

    assert created_kwargs.get("source_message") == msgs[-1]


@pytest.mark.asyncio
async def test_batch_assignee_not_found_skipped(mock_db_message):
    from core.services.task_service import TaskService

    msg = mock_db_message(msg_id=1, text="@unknown_user сделай отчёт", author_username="boss")
    mock_llm = AsyncMock()
    mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[{
        "title": "сделать отчёт", "assignees": ["@unknown_user"],
        "due_date": None, "description": "",
    }])

    with patch("core.services.task_service.user_can_create", return_value=True):
        with patch("core.services.task_service.Task") as MockTask:
            MockTask.objects.create = MagicMock(return_value=MagicMock(id=1))
            with patch("core.services.task_service._find_user_by_username", return_value=None):
                with patch("core.services.task_service.TaskAssignee") as MockTA:
                    MockTA.objects.create = MagicMock()
                    service = TaskService(llm=mock_llm)
                    result = await service.extract_tasks_from_messages_batch([msg])

    assert len(result) == 1
    MockTA.objects.create.assert_not_called()
