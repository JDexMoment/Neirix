import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch
from django.utils import timezone


def _make_mock_task(task_id=1, title="Task"):
    task = MagicMock()
    task.id = task_id
    task.title = title
    task.status = "open"
    task.recurrence_group_id = None
    task.subtasks = MagicMock()
    task.subtasks.all.return_value = []
    task.subtasks.count.return_value = 0
    return task


def _make_mock_subtask(sub_id=1, title="Subtask", status="pending", due_date=None):
    sub = MagicMock()
    sub.id = sub_id
    sub.title = title
    sub.status = status
    sub.due_date = due_date
    sub.completed_at = None
    sub.parent_task = MagicMock()
    sub.parent_task.id = 1
    sub.assignees = MagicMock()
    sub.assignees.all.return_value = []
    sub.assignees.clear = MagicMock()
    sub.assignees.add = MagicMock()
    sub.save = MagicMock()
    return sub


def _make_mock_user(user_id=1, username="user1", full_name="User One"):
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.full_name = full_name
    return user


class TestCreateSubtasksForTask:
    """Тесты для создания подзадач."""

    @pytest.mark.asyncio
    async def test_create_subtasks_from_dict_list(self):
        from core.services.subtask_service import create_subtasks_for_task
        task = _make_mock_task()
        
        subtasks_data = [
            {"title": "Подзадача 1", "assignees": ["@user1"], "due_date": "2026-08-25"},
            {"title": "Подзадача 2", "assignees": [], "due_date": None},
        ]
        
        with patch("core.services.subtask_service.SubTask.objects.create") as mock_create, \
             patch("core.services.subtask_service._find_user_by_username") as mock_find, \
             patch("core.services.subtask_service.sync_to_async") as mock_s2a:
            mock_subtask = _make_mock_subtask()
            mock_create.return_value = mock_subtask
            mock_find.return_value = _make_mock_user()
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await create_subtasks_for_task(task, subtasks_data)
            
            assert len(result) == 2
            assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_create_subtasks_from_string_list(self):
        from core.services.subtask_service import create_subtasks_for_task
        task = _make_mock_task()
        
        subtasks_data = [
            "написать код @user1 до 25.08",
            "протестировать @user2 до 30",
        ]
        
        with patch("core.services.subtask_service.SubTask.objects.create") as mock_create, \
             patch("core.services.subtask_service._find_user_by_username") as mock_find, \
             patch("core.services.subtask_service.sync_to_async") as mock_s2a:
            mock_subtask = _make_mock_subtask()
            mock_create.return_value = mock_subtask
            mock_find.return_value = _make_mock_user()
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await create_subtasks_for_task(task, subtasks_data)
            
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_create_subtasks_empty_list(self):
        from core.services.subtask_service import create_subtasks_for_task
        task = _make_mock_task()
        
        result = await create_subtasks_for_task(task, [])
        
        assert result == []

    @pytest.mark.asyncio
    async def test_create_subtasks_skips_empty_title(self):
        from core.services.subtask_service import create_subtasks_for_task
        task = _make_mock_task()
        
        subtasks_data = [
            {"title": "", "assignees": [], "due_date": None},
            {"title": "Valid", "assignees": [], "due_date": None},
        ]
        
        with patch("core.services.subtask_service.SubTask.objects.create") as mock_create, \
             patch("core.services.subtask_service.sync_to_async") as mock_s2a:
            mock_subtask = _make_mock_subtask()
            mock_create.return_value = mock_subtask
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await create_subtasks_for_task(task, subtasks_data)
            
            assert len(result) == 1
            assert mock_create.call_count == 1


class TestAddSubtask:
    """Тесты для добавления одной подзадачи."""

    @pytest.mark.asyncio
    async def test_add_subtask_success(self):
        from core.services.subtask_service import add_subtask
        task = _make_mock_task()
        
        with patch("core.services.subtask_service.Task.objects.get") as mock_get, \
             patch("core.services.subtask_service.SubTask.objects.create") as mock_create, \
             patch("core.services.subtask_service._find_user_by_username") as mock_find, \
             patch("core.services.subtask_service.sync_to_async") as mock_s2a:
            mock_get.return_value = task
            mock_subtask = _make_mock_subtask()
            mock_create.return_value = mock_subtask
            mock_find.return_value = _make_mock_user()
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await add_subtask(
                task_id=1,
                title="Новая подзадача",
                due_date_str="2026-08-25",
                assignee_usernames=["@user1"]
            )
            
            assert result is not None
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_subtask_empty_title_returns_none(self):
        from core.services.subtask_service import add_subtask
        
        result = await add_subtask(task_id=1, title="")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_add_subtask_task_not_found(self):
        from core.services.subtask_service import add_subtask, Task

        def fake_s2a(func):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        with patch("core.services.subtask_service.Task.objects") as mock_objects, \
            patch("core.services.subtask_service.sync_to_async", side_effect=fake_s2a):
            mock_objects.get.side_effect = Task.DoesNotExist()  # ← именно Task.DoesNotExist

            result = await add_subtask(task_id=999, title="Test")

            assert result is None


class TestSetSubtaskStatus:
    """Тесты для изменения статуса подзадачи."""

    @pytest.mark.asyncio
    async def test_set_status_pending_to_in_progress(self):
        from core.services.subtask_service import set_subtask_status
        subtask = _make_mock_subtask(status="pending")
        task = _make_mock_task()
        task.subtasks.all.return_value = [subtask]
        subtask.parent_task = task
        
        with patch("core.services.subtask_service.SubTask.objects.get") as mock_get, \
             patch("core.services.subtask_service.sync_to_async") as mock_s2a:
            mock_get.return_value = subtask
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await set_subtask_status(task_id=1, sub_id=1, status="in_progress")
            
            assert result is False  # Not all done yet
            assert subtask.status == "in_progress"

    @pytest.mark.asyncio
    async def test_set_status_all_done_marks_task_done(self):
        from core.services.subtask_service import set_subtask_status
        subtask1 = _make_mock_subtask(sub_id=1, status="done")
        subtask2 = _make_mock_subtask(sub_id=2, status="in_progress")
        task = _make_mock_task()
        task.subtasks.all.return_value = [subtask1, subtask2]
        task.status = "open"
        task.save = MagicMock()
        subtask2.parent_task = task
        
        with patch("core.services.subtask_service.SubTask.objects.get") as mock_get, \
             patch("core.services.subtask_service.sync_to_async") as mock_s2a:
            mock_get.return_value = subtask2
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await set_subtask_status(task_id=1, sub_id=2, status="done")
            
            assert result is True  # All done
            assert task.status == "done"
            task.save.assert_called()

    @pytest.mark.asyncio
    async def test_set_status_invalid_status_returns_false(self):
        from core.services.subtask_service import set_subtask_status
        
        result = await set_subtask_status(task_id=1, sub_id=1, status="invalid")
        
        assert result is False


class TestAssignSubtaskUsers:
    """Тесты для назначения исполнителей подзадачи."""

    @pytest.mark.asyncio
    async def test_assign_users_success(self):
        from core.services.subtask_service import assign_subtask_users
        subtask = _make_mock_subtask()
        user = _make_mock_user()
        
        with patch("core.services.subtask_service.SubTask.objects.get") as mock_get, \
             patch("core.services.subtask_service._find_user_by_username") as mock_find, \
             patch("core.services.subtask_service.sync_to_async") as mock_s2a:
            mock_get.return_value = subtask
            mock_find.return_value = user
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await assign_subtask_users(
                task_id=1,
                sub_id=1,
                usernames=["@user1"]
            )
            
            assert result is True
            subtask.assignees.clear.assert_called_once()
            subtask.assignees.add.assert_called_once_with(user)

    @pytest.mark.asyncio
    async def test_assign_users_not_found_skipped(self):
        from core.services.subtask_service import assign_subtask_users
        subtask = _make_mock_subtask()
        
        with patch("core.services.subtask_service.SubTask.objects.get") as mock_get, \
             patch("core.services.subtask_service._find_user_by_username") as mock_find, \
             patch("core.services.subtask_service.sync_to_async") as mock_s2a:
            mock_get.return_value = subtask
            mock_find.return_value = None  # User not found
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await assign_subtask_users(
                task_id=1,
                sub_id=1,
                usernames=["@unknown"]
            )
            
            assert result is True
            subtask.assignees.add.assert_not_called()


class TestBuildSubtasksSection:
    """Тесты для рендеринга блока подзадач."""

    def test_build_section_with_subtasks(self):
        from core.services.subtask_service import build_subtasks_section
        task = _make_mock_task(title="Main Task")
        
        sub1 = _make_mock_subtask(1, "Done task", status="done")
        sub2 = _make_mock_subtask(2, "In progress task", status="in_progress")
        sub3 = _make_mock_subtask(3, "Pending task", status="pending")
        
        task.subtasks.all.return_value = [sub1, sub2, sub3]
        
        result = build_subtasks_section(task)
        
        assert "Main Task" in result
        assert "✅" in result  # Done
        assert "🔄" in result  # In progress
        assert "⬜️" in result  # Pending
        assert "33%" in result or "33" in result  # 1 out of 3 done

    def test_build_section_no_subtasks_returns_empty(self):
        from core.services.subtask_service import build_subtasks_section
        task = _make_mock_task()
        task.subtasks.all.return_value = []
        
        result = build_subtasks_section(task)
        
        assert result == ""

    def test_build_section_shows_progress_bar(self):
        from core.services.subtask_service import build_subtasks_section
        task = _make_mock_task()
        
        subs = [
            _make_mock_subtask(1, "Task 1", status="done"),
            _make_mock_subtask(2, "Task 2", status="done"),
            _make_mock_subtask(3, "Task 3", status="pending"),
            _make_mock_subtask(4, "Task 4", status="pending"),
        ]
        task.subtasks.all.return_value = subs
        
        result = build_subtasks_section(task)
        
        assert "█" in result  # Filled blocks
        assert "░" in result  # Empty blocks
        assert "50%" in result  # 2 out of 4 done

    def test_build_section_shows_due_dates(self):
        from core.services.subtask_service import build_subtasks_section
        task = _make_mock_task()

        # 12:00 UTC — в любом локальном поясе (от -11 до +11) останется 25.08
        due = datetime(2026, 8, 25, 12, 0, 0, tzinfo=dt_timezone.utc)
        sub = _make_mock_subtask(1, "Task with due", status="pending", due_date=due)
        task.subtasks.all.return_value = [sub]

        result = build_subtasks_section(task)

        assert "25.08.2026" in result

    def test_build_section_shows_assignees(self):
        from core.services.subtask_service import build_subtasks_section
        task = _make_mock_task()
        
        user = _make_mock_user(username="user1")
        sub = _make_mock_subtask(1, "Assigned task", status="pending")
        sub.assignees.all.return_value = [user]
        task.subtasks.all.return_value = [sub]
        
        result = build_subtasks_section(task)
        
        assert "@user1" in result


class TestProgressPercent:
    """Тесты для вычисления процента выполнения."""

    def test_progress_percent_no_subtasks(self):
        from core.services.subtask_service import progress_percent
        task = _make_mock_task()
        task.subtasks.all.return_value = []
        
        result = progress_percent(task)
        
        assert result == 0

    def test_progress_percent_all_done(self):
        from core.services.subtask_service import progress_percent
        task = _make_mock_task()
        
        subs = [
            _make_mock_subtask(1, status="done"),
            _make_mock_subtask(2, status="done"),
            _make_mock_subtask(3, status="done"),
        ]
        task.subtasks.all.return_value = subs
        
        result = progress_percent(task)
        
        assert result == 100

    def test_progress_percent_partial(self):
        from core.services.subtask_service import progress_percent
        task = _make_mock_task()
        
        subs = [
            _make_mock_subtask(1, status="done"),
            _make_mock_subtask(2, status="done"),
            _make_mock_subtask(3, status="pending"),
            _make_mock_subtask(4, status="in_progress"),
        ]
        task.subtasks.all.return_value = subs
        
        result = progress_percent(task)
        
        assert result == 50  # 2 out of 4 done


class TestParseSubtaskInput:
    """Тесты для парсинга ввода подзадачи."""

    def test_parse_input_with_username_and_due(self):
        from bot.handlers.subtasks import _parse_subtask_input
        text = "написать код @user1 до 25.08"
        
        result = _parse_subtask_input(text)
        
        assert result["title"] == "написать код"
        assert "@user1" in result["users"] or "user1" in result["users"]
        assert result["due"] is not None
        assert "2026-08-25" in result["due"]

    def test_parse_input_only_title(self):
        from bot.handlers.subtasks import _parse_subtask_input
        text = "просто задача"
        
        result = _parse_subtask_input(text)
        
        assert result["title"] == "просто задача"
        assert result["users"] == []
        assert result["due"] is None

    def test_parse_input_multiple_users(self):
        from bot.handlers.subtasks import _parse_subtask_input
        text = "задача @user1 @user2"
        
        result = _parse_subtask_input(text)
        
        assert "user1" in result["users"]
        assert "user2" in result["users"]

    def test_parse_input_due_date_only(self):
        from bot.handlers.subtasks import _parse_subtask_input
        text = "сделать до 30.08"
        
        result = _parse_subtask_input(text)
        
        assert result["title"] == "сделать"
        assert result["due"] is not None


class TestSubtaskHandlers:
    """Тесты для хендлеров подзадач."""

    @pytest.mark.asyncio
    async def test_cb_task_add_subtask_creates_helper(self):
        from bot.handlers.subtasks import cb_task_add_subtask
        task = _make_mock_task()
        
        callback = MagicMock()
        callback.data = "task_add_subtask:1"
        callback.from_user.id = 123
        callback.message = MagicMock()
        callback.message.answer = AsyncMock()
        callback.message.chat.id = -100
        callback.message.message_id = 1
        callback.answer = AsyncMock()
        
        with patch("bot.handlers.subtasks._reload_task_async") as mock_reload, \
             patch("bot.handlers.subtasks._set_pending") as mock_set:
            mock_reload.return_value = task
            
            await cb_task_add_subtask(callback)
            
            callback.message.answer.assert_called_once()
            mock_set.assert_called_once()
            callback.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_cb_task_sub_add_cancel_clears_pending(self):
        from bot.handlers.subtasks import cb_task_sub_add_cancel
        
        callback = MagicMock()
        callback.data = "task_sub_add_cancel:1"
        callback.from_user.id = 123
        callback.message = MagicMock()
        callback.message.delete = AsyncMock()
        callback.answer = AsyncMock()
        
        with patch("bot.handlers.subtasks._clear_pending") as mock_clear:
            await cb_task_sub_add_cancel(callback)
            
            mock_clear.assert_called_once_with(123)
            callback.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_cb_task_sub_toggle_changes_status(self):
        from bot.handlers.subtasks import cb_task_sub_toggle
        task = _make_mock_task()
        subtask = _make_mock_subtask(status="pending")
        
        callback = MagicMock()
        callback.data = "task_sub_toggle:1:1"
        callback.from_user.id = 123
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        
        with patch("bot.handlers.subtasks.SubTask.objects.filter") as mock_filter, \
             patch("bot.handlers.subtasks._reload_task_async") as mock_reload, \
             patch("bot.handlers.subtasks.TelegramUser.objects.filter") as mock_user_filter, \
             patch("bot.handlers.subtasks._can_toggle") as mock_can, \
             patch("bot.handlers.subtasks.set_subtask_status") as mock_set, \
             patch("bot.handlers.subtasks.sync_to_async") as mock_s2a:
            mock_filter.return_value.prefetch_related.return_value.first.return_value = subtask
            mock_reload.return_value = task
            mock_user_filter.return_value.first.return_value = MagicMock()
            mock_can.return_value = True
            mock_set.return_value = False
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            await cb_task_sub_toggle(callback)
            
            mock_set.assert_called_once_with(1, 1, "in_progress")
            callback.message.edit_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_cb_task_sub_toggle_no_permission(self):
        from bot.handlers.subtasks import cb_task_sub_toggle
        subtask = _make_mock_subtask()

        callback = MagicMock()
        callback.data = "task_sub_toggle:1:1"
        callback.from_user.id = 123
        callback.answer = AsyncMock()

        with patch("bot.handlers.subtasks.SubTask.objects.filter") as mock_filter, \
            patch("bot.handlers.subtasks._reload_task_async") as mock_reload, \
            patch("bot.handlers.subtasks.TelegramUser.objects.filter") as mock_user_filter, \
            patch("bot.handlers.subtasks._can_toggle") as mock_can, \
            patch("bot.handlers.subtasks.sync_to_async") as mock_s2a:
            mock_filter.return_value.prefetch_related.return_value.first.return_value = subtask
            mock_reload.return_value = _make_mock_task()
            mock_user_filter.return_value.first.return_value = MagicMock()
            mock_can.return_value = False  # No permission
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())

            await cb_task_sub_toggle(callback)

            callback.answer.assert_called_once()
            # Текст находится в позиционном аргументе [0][0], а не в show_alert
            assert "Нет прав" in callback.answer.call_args[0][0]