import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch
from django.utils import timezone
from tests.conftest import make_message


def _make_mock_task(task_id=1, title="Task", priority="normal", due_date=None):
    task = MagicMock()
    task.id = task_id
    task.title = title
    task.priority = priority
    task.due_date = due_date
    task.status = "open"
    task.recurrence_group_id = None
    task.subtasks = MagicMock()
    task.subtasks.all.return_value = []
    task.assignees = MagicMock()
    task.assignees.all.return_value = []
    task.creator = MagicMock()
    task.creator.username = "creator"
    task.creator.full_name = "Creator"
    return task


@pytest.fixture
def mock_task_service():
    with patch("bot.handlers.tasks.task_service") as mock:
        yield mock


class TestPriorityEmoji:
    """Тесты для отображения эмодзи приоритета."""

    def test_critical_priority_shows_red_circle(self):
        from bot.handlers.tasks import _get_priority_emoji
        task = _make_mock_task(priority="critical")
        # В коде есть пробел после эмодзи для разделения от названия задачи
        assert _get_priority_emoji(task) == "🔴 "

    def test_high_priority_shows_yellow_circle(self):
        from bot.handlers.tasks import _get_priority_emoji
        task = _make_mock_task(priority="high")
        assert _get_priority_emoji(task) == "🟡 "

    def test_normal_priority_shows_nothing(self):
        from bot.handlers.tasks import _get_priority_emoji
        task = _make_mock_task(priority="normal")
        assert _get_priority_emoji(task) == ""

    def test_low_priority_shows_white_circle(self):
        from bot.handlers.tasks import _get_priority_emoji
        task = _make_mock_task(priority="low")
        assert _get_priority_emoji(task) == "⚪ "

    def test_none_priority_defaults_to_normal(self):
        from bot.handlers.tasks import _get_priority_emoji
        task = _make_mock_task(priority=None)
        assert _get_priority_emoji(task) == ""


class TestPriorityFiltering:
    """Тесты для фильтрации задач по приоритету."""

    def test_important_filter_includes_critical_and_high(self):
        from bot.handlers.tasks import _apply_task_filters
        tasks = [
            _make_mock_task(1, "Critical", priority="critical"),
            _make_mock_task(2, "High", priority="high"),
            _make_mock_task(3, "Normal", priority="normal"),
            _make_mock_task(4, "Low", priority="low"),
        ]
        filters = {"important": True}
        user = MagicMock()
        result = _apply_task_filters(tasks, filters, user)
        assert len(result) == 2
        assert result[0].title == "Critical"
        assert result[1].title == "High"

    def test_important_filter_excludes_normal_and_low(self):
        from bot.handlers.tasks import _apply_task_filters
        tasks = [
            _make_mock_task(1, "Normal", priority="normal"),
            _make_mock_task(2, "Low", priority="low"),
        ]
        filters = {"important": True}
        user = MagicMock()
        result = _apply_task_filters(tasks, filters, user)
        assert len(result) == 0

    def test_no_priority_filter_shows_all(self):
        from bot.handlers.tasks import _apply_task_filters
        tasks = [
            _make_mock_task(1, "Critical", priority="critical"),
            _make_mock_task(2, "Normal", priority="normal"),
        ]
        filters = {}
        user = MagicMock()
        result = _apply_task_filters(tasks, filters, user)
        assert len(result) == 2


class TestPriorityTextRendering:
    """Тесты для отображения приоритета в тексте задачи."""

    def test_critical_task_text_contains_red_circle(self):
        from bot.handlers.tasks import _build_task_text
        task = _make_mock_task(priority="critical", title="Important Task")
        text = _build_task_text(task)
        assert "🔴" in text
        assert "Important Task" in text

    def test_normal_task_text_no_priority_emoji(self):
        from bot.handlers.tasks import _build_task_text
        task = _make_mock_task(priority="normal", title="Regular Task")
        text = _build_task_text(task)
        assert "🔴" not in text
        assert "🟡" not in text
        assert "⚪" not in text
        assert "Regular Task" in text


class TestPriorityCycleCallback:
    """Тесты для циклического изменения приоритета."""

    @pytest.fixture(autouse=True)
    def _patch_sync_and_task_model(self):
        """
        Патчим sync_to_async (чтобы не запускал реальный поток)
        и Task.objects (так как импорт локальный — патчим в источнике).
        """
        def fake_s2a(func):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        with patch("bot.handlers.tasks.sync_to_async", side_effect=fake_s2a), \
             patch("core.models.Task.objects") as mock_task_objects:
            mock_task_objects.filter.return_value.update = MagicMock(return_value=1)
            self._mock_task_objects = mock_task_objects
            yield

    @pytest.mark.asyncio
    async def test_cycle_normal_to_high(self, mock_task_service):
        from bot.handlers.tasks import callback_task_cycle_priority
        task = _make_mock_task(priority="normal")
        mock_task_service.get_task_by_id = AsyncMock(return_value=task)

        callback = MagicMock()
        callback.data = "task_cycle_priority:1"
        callback.answer = AsyncMock()

        await callback_task_cycle_priority(callback)

        callback.answer.assert_called_once()
        assert "high" in callback.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cycle_high_to_critical(self, mock_task_service):
        from bot.handlers.tasks import callback_task_cycle_priority
        task = _make_mock_task(priority="high")
        mock_task_service.get_task_by_id = AsyncMock(return_value=task)

        callback = MagicMock()
        callback.data = "task_cycle_priority:1"
        callback.answer = AsyncMock()

        await callback_task_cycle_priority(callback)

        callback.answer.assert_called_once()
        assert "critical" in callback.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cycle_critical_to_low(self, mock_task_service):
        from bot.handlers.tasks import callback_task_cycle_priority
        task = _make_mock_task(priority="critical")
        mock_task_service.get_task_by_id = AsyncMock(return_value=task)

        callback = MagicMock()
        callback.data = "task_cycle_priority:1"
        callback.answer = AsyncMock()

        await callback_task_cycle_priority(callback)

        callback.answer.assert_called_once()
        assert "low" in callback.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cycle_low_to_normal(self, mock_task_service):
        from bot.handlers.tasks import callback_task_cycle_priority
        task = _make_mock_task(priority="low")
        mock_task_service.get_task_by_id = AsyncMock(return_value=task)

        callback = MagicMock()
        callback.data = "task_cycle_priority:1"
        callback.answer = AsyncMock()

        await callback_task_cycle_priority(callback)

        callback.answer.assert_called_once()
        assert "normal" in callback.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cycle_task_not_found(self, mock_task_service):
        from bot.handlers.tasks import callback_task_cycle_priority
        mock_task_service.get_task_by_id = AsyncMock(return_value=None)

        callback = MagicMock()
        callback.data = "task_cycle_priority:999"
        callback.answer = AsyncMock()

        await callback_task_cycle_priority(callback)

        callback.answer.assert_called_once()
        call_kwargs = callback.answer.call_args
        assert "не найдена" in (call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("text", ""))