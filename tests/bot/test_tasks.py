import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch
from django.utils import timezone
from asgiref.sync import sync_to_async

from tests.conftest import make_message


def _make_mock_task(key, title, task_id=1, due_date=None, assignees=None):
    task = MagicMock()
    task.id = task_id
    task.title = title
    task.due_date = due_date
    task.status = "open"
    task.priority = 'normal'
    task.recurrence_group_id = None  # Явно задаем None, чтобы не было MagicMock
    
    # Мокаем создателя, чтобы в тексте не было <MagicMock>
    task.creator = MagicMock()
    task.creator.username = None
    task.creator.full_name = "System"
    
    # Мокаем подзадачи, чтобы list() не падал
    task.subtasks = MagicMock()
    task.subtasks.all.return_value = [] 
    
    links = []
    for user in (assignees or []):
        link = MagicMock()
        link.user = user
        links.append(link)
    task.assignees.all.return_value = links
    return task


def _make_mock_user(username=None, full_name="Unknown", user_id=1):
    u = MagicMock()
    u.id = user_id
    u.username = username
    u.full_name = full_name
    return u

@pytest.fixture
def mock_get_comment_counts():
    """Мокает подсчет комментариев, чтобы не было реальных запросов к БД."""
    with patch("bot.handlers.comments._get_comment_counts", new_callable=AsyncMock) as mock:
        mock.return_value = {}
        yield mock

@pytest.fixture
def mock_get_recurrence_text():
    """Мокает загрузку текста повторения, чтобы не было реальных запросов к БД."""
    with patch("bot.handlers.tasks._get_recurrence_text", new_callable=AsyncMock) as mock:
        mock.return_value = None
        yield mock

@pytest.fixture
def tasks_with_assignees():
    u1 = _make_mock_user(username="user1", full_name="User One")
    u2 = _make_mock_user(username=None, full_name="User Two")
    # Задаем due_date для обеих задач, чтобы гарантировать предсказуемый порядок при сортировке
    return [
        _make_mock_task("report", "Сделать отчёт", task_id=1,
                        due_date=datetime.now(dt_timezone.utc) + timedelta(days=2),
                        assignees=[u1]),
        _make_mock_task("pres", "Подготовить презентацию", task_id=2,
                        due_date=datetime.now(dt_timezone.utc) + timedelta(days=3),
                        assignees=[u1, u2]),
    ]


@pytest.fixture
def tasks_no_assignees():
    return [_make_mock_task("common", "Общая задача", task_id=3)]


@pytest.fixture
def mock_get_chat_context_tasks():
    with patch("bot.handlers.tasks.get_chat_context") as mock:
        yield mock


@pytest.fixture
def mock_sync_tasks():
    with patch("bot.handlers.tasks.sync_to_async") as mock_s2a:
        yield mock_s2a


def _setup_sync_mock(mock_s2a, return_value):
    async def fake_fetch(*a, **kw):
        return return_value
    mock_s2a.return_value = fake_fetch


# ══════════════════════════════════════════════════════════════════
# /tasks — список задач
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_tasks_private_with_tasks(
    private_chat, telegram_user, now_dt,
    tasks_with_assignees,
    mock_get_chat_context_tasks, mock_sync_tasks,
    mock_get_comment_counts, mock_get_recurrence_text,
):
    from bot.handlers.tasks import cmd_tasks
    msg = make_message(private_chat, telegram_user, "/tasks", now_dt)
    mock_chat = MagicMock()
    mock_db_user = MagicMock()
    mock_get_chat_context_tasks.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_tasks, tasks_with_assignees)
    await cmd_tasks(msg)
    assert msg.answer.call_count == 3
    texts = [c.args[0] if c.args else c.kwargs.get("text", "") for c in msg.answer.call_args_list]
    assert "📋 Ваши задачи" in texts[0]
    assert "Сделать отчёт" in texts[1]
    assert "@user1" in texts[1]
    assert "Подготовить презентацию" in texts[2]
    assert "User Two" in texts[2]


@pytest.mark.asyncio
async def test_tasks_private_no_tasks(
    private_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks
    msg = make_message(private_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [])
    await cmd_tasks(msg)
    assert "Нет задач" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_tasks_group_with_tasks(
    group_chat, telegram_user, now_dt,
    tasks_with_assignees,
    mock_get_chat_context_tasks, mock_sync_tasks,
    mock_get_comment_counts, mock_get_recurrence_text,
):
    from bot.handlers.tasks import cmd_tasks
    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_chat = MagicMock()
    mock_chat.title = "Test Group"
    mock_get_chat_context_tasks.return_value = (mock_chat, None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, tasks_with_assignees)
    await cmd_tasks(msg)
    assert msg.answer.call_count == 3
    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    assert "📋 Задачи чата Test Group" in texts[0]
    assert "Сделать отчёт" in texts[1]
    assert "Подготовить презентацию" in texts[2]


@pytest.mark.asyncio
async def test_tasks_no_assignees_shown(
    group_chat, telegram_user, now_dt,
    tasks_no_assignees,
    mock_get_chat_context_tasks, mock_sync_tasks,
    mock_get_comment_counts, mock_get_recurrence_text,
):
    from bot.handlers.tasks import cmd_tasks
    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_chat = MagicMock()
    mock_chat.title = "Test Group"
    mock_get_chat_context_tasks.return_value = (mock_chat, None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, tasks_no_assignees)
    await cmd_tasks(msg)
    assert msg.answer.call_count == 2
    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    assert "Общая задача" in texts[1]
    assert "не назначен" in texts[1]


@pytest.mark.asyncio
async def test_tasks_no_user(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks,
):
    from bot.handlers.tasks import cmd_tasks
    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(), None, None)
    await cmd_tasks(msg)
    assert "Не удалось определить пользователя" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_tasks_due_date_formatted(
    private_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
    mock_get_comment_counts, mock_get_recurrence_text,
):
    from bot.handlers.tasks import cmd_tasks
    task = _make_mock_task("dated", "Задача со сроком", task_id=10,
                           due_date=datetime(2026, 5, 15, 12, 0, 0, tzinfo=dt_timezone.utc))
    msg = make_message(private_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task])
    await cmd_tasks(msg)
    assert msg.answer.call_count == 2
    text = msg.answer.call_args_list[1].args[0] if msg.answer.call_args_list[1].args else ""
    assert "📅 до" in text or "Просрочено" in text


# ══════════════════════════════════════════════════════════════════
# BATCH TASK EXTRACTION TESTS  (все DB-взаимодействия замоканы)
# ══════════════════════════════════════════════════════════════════

def _make_proper_sync_to_async():
    def proper_s2a(func):
        async def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return proper_s2a


@pytest.fixture
def mock_db_message_factory():
    def _create(msg_id=1, text="", username="testuser", full_name="Test User",
                telegram_id=12345, chat_id=-100123, thread_id=0, timestamp=None):
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
        msg.author.username = username
        msg.author.full_name = full_name
        msg.author.telegram_id = telegram_id
        msg.author.is_bot = False
        msg.chat = MagicMock()
        msg.chat.chat_id = chat_id
        msg.chat.type = "supergroup"
        msg.topic = MagicMock()
        msg.topic.thread_id = thread_id
        return msg
    return _create


class TestBatchTaskExtraction:

    @pytest.mark.asyncio
    async def test_batch_empty_messages(self):
        from core.services.task_service import TaskService
        service = TaskService(llm=MagicMock())
        result = await service.extract_tasks_from_messages_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_single_message_with_task(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        msg = mock_db_message_factory(msg_id=1, text="сделай отчёт к пятнице", username="boss")
        mock_llm = AsyncMock()
        mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[{
            "title": "сделать отчёт", "assignees": [], "due_date": None, "description": "",
        }])

        with patch("core.services.task_service.user_can_create", return_value=True):
            with patch("core.services.task_service.Task") as MockTask:
                mock_task_instance = MagicMock()
                mock_task_instance.id = 1
                MockTask.objects.create = MagicMock(return_value=mock_task_instance)
                service = TaskService(llm=mock_llm)
                result = await service.extract_tasks_from_messages_batch([msg])

        assert len(result) == 1
        mock_llm.extract_tasks_from_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_multiple_messages_combined_context(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        now = timezone.now()
        msgs = [
            mock_db_message_factory(msg_id=1, text="нужно сделать отчёт", username="boss",
                                    timestamp=now - timedelta(minutes=2)),
            mock_db_message_factory(msg_id=2, text="@worker1 возьми на себя", username="boss",
                                    timestamp=now - timedelta(minutes=1)),
            mock_db_message_factory(msg_id=3, text="ок, сделаю", username="worker1", timestamp=now),
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

        assert mock_llm.extract_tasks_from_messages.call_count == 1
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_batch_no_tasks_found(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        msg = mock_db_message_factory(msg_id=1, text="привет", username="user1")
        mock_llm = AsyncMock()
        mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[])
        service = TaskService(llm=mock_llm)
        result = await service.extract_tasks_from_messages_batch([msg])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_llm_error_returns_empty(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        msg = mock_db_message_factory(msg_id=1, text="сделай отчёт", username="boss")
        mock_llm = AsyncMock()
        mock_llm.extract_tasks_from_messages = AsyncMock(side_effect=Exception("API error"))
        service = TaskService(llm=mock_llm)
        result = await service.extract_tasks_from_messages_batch([msg])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_multiple_tasks_from_batch(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        now = timezone.now()
        msgs = [
            mock_db_message_factory(msg_id=1, text="@worker1 сделай отчёт", username="boss",
                                    timestamp=now - timedelta(minutes=1)),
            mock_db_message_factory(msg_id=2, text="@worker2 подготовь презентацию", username="boss",
                                    timestamp=now),
        ]
        mock_llm = AsyncMock()
        mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[
            {"title": "сделать отчёт", "assignees": [], "due_date": None, "description": ""},
            {"title": "подготовить презентацию", "assignees": [], "due_date": None, "description": ""},
        ])
        task_counter = {"count": 0}
        def make_task(**kwargs):
            task_counter["count"] += 1
            t = MagicMock()
            t.id = task_counter["count"]
            return t

        with patch("core.services.task_service.user_can_create", return_value=True):
            with patch("core.services.task_service.Task") as MockTask:
                MockTask.objects.create = MagicMock(side_effect=make_task)
                service = TaskService(llm=mock_llm)
                result = await service.extract_tasks_from_messages_batch(msgs)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_batch_source_message_is_last(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        now = timezone.now()
        msgs = [
            mock_db_message_factory(msg_id=1, text="нужно сделать отчёт", username="boss",
                                    timestamp=now - timedelta(minutes=1)),
            mock_db_message_factory(msg_id=2, text="@worker1 возьми", username="boss", timestamp=now),
        ]
        mock_llm = AsyncMock()
        mock_llm.extract_tasks_from_messages = AsyncMock(return_value=[{
            "title": "сделать отчёт", "assignees": [], "due_date": None, "description": "",
        }])
        created_kwargs = {}
        def capture_create(**kwargs):
            created_kwargs.update(kwargs)
            t = MagicMock()
            t.id = 1
            return t

        with patch("core.services.task_service.user_can_create", return_value=True):
            with patch("core.services.task_service.Task") as MockTask:
                MockTask.objects.create = MagicMock(side_effect=capture_create)
                service = TaskService(llm=mock_llm)
                await service.extract_tasks_from_messages_batch(msgs)

        assert created_kwargs.get("source_message") == msgs[-1]

    @pytest.mark.asyncio
    async def test_batch_assignee_not_found_skipped(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        msg = mock_db_message_factory(msg_id=1, text="@unknown_user сделай отчёт", username="boss")
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


# ══════════════════════════════════════════════════════════════════
# DUPLICATE PREVENTION TESTS  (с реальной БД)
# ══════════════════════════════════════════════════════════════════

def _make_naive_due_date(days_ahead=2):
    from datetime import datetime as dt_mod
    naive = dt_mod.now() + timedelta(days=days_ahead)
    return naive.replace(hour=23, minute=59, second=0, microsecond=0)


def _make_aware_due_date(days_ahead=2):
    naive = _make_naive_due_date(days_ahead)
    return timezone.make_aware(naive, timezone.get_current_timezone())


class TestTaskDuplicatePrevention:

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_create_task_duplicate_same_title_due_date_assignees(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        from core.models import Task, TelegramUser, TelegramChat, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-2001, title="Test Chat")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        author = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=254321, username="manager", full_name="Manager")
        assignee = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=22345, username="taskuser", full_name="Task User")
        # ═══ ДАЁМ ПРАВА ═══
        await sync_to_async(UserRole.objects.create)(user=author, chat=chat, role="admin")

        real_msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=10, chat=chat, topic=topic, author=author,
            text="задача для @taskuser", timestamp=timezone.now())

        task_data = {"title": "Выполнить задание", "assignees": ["@taskuser"],
                     "due_date": _make_naive_due_date().strftime("%Y-%m-%d"), "description": ""}
        service = TaskService()
        first_task = await service._create_task_from_data(task_data, real_msg)
        second_task = await service._create_task_from_data(task_data, real_msg)

        assert first_task is not None
        assert second_task is not None
        assert first_task.id == second_task.id

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_create_task_different_title_no_duplicate(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        from core.models import TelegramUser, TelegramChat, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-2002, title="Test Chat 2")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        author = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=254322, username="manager2", full_name="Manager 2")
        await sync_to_async(TelegramUser.objects.create)(
            telegram_id=22346, username="taskuser2", full_name="Task User 2")
        # ═══ ДАЁМ ПРАВА ═══
        await sync_to_async(UserRole.objects.create)(user=author, chat=chat, role="admin")

        real_msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=20, chat=chat, topic=topic, author=author,
            text="разные задачи", timestamp=timezone.now())

        due_date_str = _make_naive_due_date().strftime("%Y-%m-%d")
        data1 = {"title": "Первая задача", "assignees": ["@taskuser2"],
                 "due_date": due_date_str, "description": ""}
        data2 = {"title": "Вторая задача", "assignees": ["@taskuser2"],
                 "due_date": due_date_str, "description": ""}
        service = TaskService()
        first = await service._create_task_from_data(data1, real_msg)
        second = await service._create_task_from_data(data2, real_msg)
        assert first is not None
        assert second is not None
        assert first.id != second.id

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_check_duplicate_task_method(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        from core.models import Task, TaskAssignee, TelegramUser, TelegramChat, Topic

        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-2003, title="Test Chat 3")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=22347, username="taskuser3", full_name="Task User 3")

        aware_due_date = _make_aware_due_date()
        existing = await sync_to_async(Task.objects.create)(
            title="Тестовая задача", topic=topic, due_date=aware_due_date, status="open")
        await sync_to_async(TaskAssignee.objects.create)(task=existing, user=user)

        service = TaskService()
        result = await sync_to_async(service._check_duplicate_task)(
            "Тестовая задача", aware_due_date, topic, [user])
        assert result is not None
        assert result.id == existing.id

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_create_task_different_assignees_no_duplicate(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        from core.models import TelegramUser, TelegramChat, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-2004, title="Test Chat 4")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        author = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=254324, username="manager4", full_name="Manager 4")
        user1 = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=22348, username="taskuser1", full_name="Task User 1")
        user2 = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=22349, username="taskuser2", full_name="Task User 2")
        # ═══ ДАЁМ ПРАВА ═══
        await sync_to_async(UserRole.objects.create)(user=author, chat=chat, role="admin")

        real_msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=40, chat=chat, topic=topic, author=author,
            text="разные исполнители", timestamp=timezone.now())

        ds = _make_naive_due_date().strftime("%Y-%m-%d")
        data1 = {"title": "Задача для первого", "assignees": ["@taskuser1"],
                 "due_date": ds, "description": ""}
        data2 = {"title": "Задача для первого", "assignees": ["@taskuser2"],
                 "due_date": ds, "description": ""}
        service = TaskService()
        first = await service._create_task_from_data(data1, real_msg)
        second = await service._create_task_from_data(data2, real_msg)
        assert first is not None
        assert second is not None
        assert first.id != second.id

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_create_task_closed_task_not_considered_duplicate(self, mock_db_message_factory):
        from core.services.task_service import TaskService
        from core.models import Task, TaskAssignee, TelegramUser, TelegramChat, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-2005, title="Test Chat 5")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        author = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=254325, username="manager5", full_name="Manager 5")
        user = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=22350, username="taskuser5", full_name="Task User 5")
        # ═══ ДАЁМ ПРАВА ═══
        await sync_to_async(UserRole.objects.create)(user=author, chat=chat, role="admin")

        aware_due_date = _make_aware_due_date()
        closed = await sync_to_async(Task.objects.create)(
            title="Тестовая задача", topic=topic, due_date=aware_due_date, status="closed")
        await sync_to_async(TaskAssignee.objects.create)(task=closed, user=user)

        real_msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=50, chat=chat, topic=topic, author=author,
            text="новая задача", timestamp=timezone.now())

        task_data = {"title": "Тестовая задача", "assignees": ["@taskuser5"],
                     "due_date": _make_naive_due_date().strftime("%Y-%m-%d"), "description": ""}
        service = TaskService()
        new_task = await service._create_task_from_data(task_data, real_msg)
        assert new_task is not None
        assert new_task.id != closed.id
