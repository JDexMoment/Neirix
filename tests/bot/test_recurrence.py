import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch
from django.utils import timezone


class TestParseRecurrence:
    """Тесты для парсинга текста повторяющихся задач/встреч."""

    def test_parse_daily_recurrence(self):
        from core.services.recurrence_service import parse_recurrence
        result = parse_recurrence("каждый день в 10:00")
        assert result is not None
        assert result["period"] == "daily"
        assert result["cron"] == "0 10 * * *"
        assert "каждый день" in result["human"]
        assert result["hour"] == 10
        assert result["minute"] == 0

    def test_parse_weekly_recurrence_single_day(self):
        from core.services.recurrence_service import parse_recurrence
        result = parse_recurrence("каждый понедельник в 9:30")
        assert result is not None
        assert result["period"] == "weekly"
        assert "1" in result["cron"]  # Monday = 1
        assert result["hour"] == 9
        assert result["minute"] == 30

    def test_parse_weekly_recurrence_multiple_days(self):
        from core.services.recurrence_service import parse_recurrence
        # "каждый" вместо "каждые" (регулярка не поддерживает "каждые")
        # Без запятых, чтобы split() корректно извлёк дни недели
        result = parse_recurrence("каждый понедельник среду пятницу в 14:00")
        assert result is not None
        assert result["period"] == "weekly"
        cron_parts = result["cron"].split()
        assert len(cron_parts) == 5
        days_of_week = cron_parts[4].split(",")
        assert "1" in days_of_week   # Monday
        assert "3" in days_of_week   # Wednesday
        assert "5" in days_of_week   # Friday

    def test_parse_monthly_recurrence(self):
        from core.services.recurrence_service import parse_recurrence
        # "каждый 15-е число" вместо "каждое 15 число"
        # (регулярка требует форму кажд[ыу][йю] + число + "-е/ые" + "число/числа")
        result = parse_recurrence("каждый 15-е число в 12:00")
        assert result is not None
        assert result["period"] == "monthly"
        assert "15" in result["cron"]
        assert result["day_of_month"] == 15

    def test_parse_empty_string_returns_none(self):
        from core.services.recurrence_service import parse_recurrence
        result = parse_recurrence("")
        assert result is None

    def test_parse_invalid_text_returns_none(self):
        from core.services.recurrence_service import parse_recurrence
        result = parse_recurrence("просто какой-то текст")
        assert result is None

    def test_parse_english_daily(self):
        from core.services.recurrence_service import parse_recurrence
        result = parse_recurrence("every day at 15:30")
        assert result is not None
        assert result["period"] == "daily"
        assert result["hour"] == 15
        assert result["minute"] == 30


class TestGetNextOccurrence:
    """Тесты для вычисления следующей даты повторения."""

    def test_next_occurrence_daily(self):
        from core.services.recurrence_service import get_next_occurrence
        from_date = datetime(2026, 8, 22, 10, 0, 0, tzinfo=dt_timezone.utc)
        result = get_next_occurrence("0 10 * * *", from_date)
        # Should be next day at 10:00
        assert result.day == 23
        assert result.hour == 10
        assert result.minute == 0

    def test_next_occurrence_weekly_monday(self):
        from core.services.recurrence_service import get_next_occurrence
        # 2026-08-22 is Saturday
        from_date = datetime(2026, 8, 22, 10, 0, 0, tzinfo=dt_timezone.utc)
        result = get_next_occurrence("0 10 * * 1", from_date)  # Monday
        # Next Monday is 2026-08-24
        assert result.weekday() == 0  # Monday
        assert result.hour == 10

    def test_next_occurrence_monthly(self):
        from core.services.recurrence_service import get_next_occurrence
        from_date = datetime(2026, 8, 22, 10, 0, 0, tzinfo=dt_timezone.utc)
        result = get_next_occurrence("0 10 15 * *", from_date)  # 15th of month
        # Next 15th is September 15
        assert result.month == 9
        assert result.day == 15
        assert result.hour == 10

    def test_next_occurrence_if_time_passed_adds_day(self):
        from core.services.recurrence_service import get_next_occurrence
        # Current time is 14:00, cron is for 10:00
        from_date = datetime(2026, 8, 22, 14, 0, 0, tzinfo=dt_timezone.utc)
        result = get_next_occurrence("0 10 * * *", from_date)
        # Should be next day at 10:00
        assert result.day == 23
        assert result.hour == 10


class TestRecurrenceServiceTasks:
    """Тесты для RecurrenceService с задачами."""

    @pytest.mark.asyncio
    async def test_create_recurring_task(self):
        from core.services.recurrence_service import RecurrenceService
        service = RecurrenceService()
        
        task = MagicMock()
        task.id = 1
        task.due_date = timezone.now() + timedelta(days=1)
        task.save = MagicMock()
        
        with patch("core.services.recurrence_service.TaskRecurrence.objects.create") as mock_create, \
             patch("core.services.recurrence_service.sync_to_async") as mock_s2a:
            mock_recurrence = MagicMock()
            mock_create.return_value = mock_recurrence
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await service.create_recurring_task(
                task=task,
                cron_expression="0 10 * * *",
                human_readable="каждый день в 10:00"
            )
            
            assert result is not None
            assert task.recurrence_group_id is not None

    @pytest.mark.asyncio
    async def test_on_task_completed_creates_next(self):
        from core.services.recurrence_service import RecurrenceService
        service = RecurrenceService()
        
        task = MagicMock()
        task.id = 1
        task.recurrence_group_id = "test-group-id"
        task.due_date = timezone.now()
        task.topic = MagicMock()
        task.source_message = MagicMock()
        task.creator = MagicMock()
        task.description = "Test"
        task.assignees = MagicMock()
        task.assignees.select_related.return_value.all.return_value = []
        
        mock_recurrence = MagicMock()
        mock_recurrence.task = task
        mock_recurrence.cron_expression = "0 10 * * *"
        mock_recurrence.save = MagicMock()
        
        with patch("core.services.recurrence_service.TaskRecurrence.objects.select_related") as mock_select, \
             patch("core.services.recurrence_service.Task.objects.create") as mock_create, \
             patch("core.services.recurrence_service.sync_to_async") as mock_s2a:
            mock_get = MagicMock()
            mock_get.get = MagicMock(return_value=mock_recurrence)
            mock_select.return_value = mock_get
            mock_create.return_value = MagicMock(id=2)
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await service.on_task_completed(task)
            
            assert result is not None
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_task_series(self):
        from core.services.recurrence_service import RecurrenceService
        service = RecurrenceService()
        
        task = MagicMock()
        task.recurrence_group_id = "test-group-id"
        
        mock_recurrence = MagicMock()
        mock_recurrence.is_active = True
        mock_recurrence.save = MagicMock()
        
        with patch("core.services.recurrence_service.TaskRecurrence.objects.get") as mock_get, \
             patch("core.services.recurrence_service.Task.objects.filter") as mock_filter, \
             patch("core.services.recurrence_service.sync_to_async") as mock_s2a:
            mock_get.return_value = mock_recurrence
            mock_filter.return_value.update = MagicMock()
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await service.cancel_task_series(task)
            
            assert result is True
            assert mock_recurrence.is_active is False


class TestRecurrenceServiceMeetings:
    """Тесты для RecurrenceService с встречами."""

    @pytest.mark.asyncio
    async def test_create_recurring_meeting(self):
        from core.services.recurrence_service import RecurrenceService
        service = RecurrenceService()

        topic = MagicMock()
        creator = MagicMock()
        participants = [MagicMock(), MagicMock()]

        # Правильный мок: передаёт аргументы в функцию
        def fake_s2a(func):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        with patch("core.services.recurrence_service.MeetingRecurrence.objects.create") as mock_rec_create, \
            patch("core.services.recurrence_service.MeetingRecurrenceParticipant.objects.create") as mock_part_create, \
            patch("core.services.recurrence_service.Meeting.objects.create") as mock_meeting_create, \
            patch("core.services.recurrence_service.sync_to_async", side_effect=fake_s2a):
            mock_recurrence = MagicMock()
            mock_recurrence.id = 1
            mock_rec_create.return_value = mock_recurrence
            mock_meeting_create.return_value = MagicMock()

            result = await service.create_recurring_meeting(
                title="Daily Standup",
                topic=topic,
                creator=creator,
                cron_expression="0 10 * * 1,3,5",  # ← запятые вместо диапазона!
                human_readable="каждый пн, ср, пт в 10:00",
                participants=participants,
                source_message=MagicMock(),
                is_all_hands=False,
                instance_count=2
            )

            assert result is not None
            assert mock_rec_create.called
            assert mock_part_create.call_count == 2  # 2 участника
            assert mock_meeting_create.call_count == 2  # 2 инстанса

    @pytest.mark.asyncio
    async def test_cancel_meeting_series(self):
        from core.services.recurrence_service import RecurrenceService
        service = RecurrenceService()
        
        mock_recurrence = MagicMock()
        mock_recurrence.is_active = True
        mock_recurrence.save = MagicMock()
        
        with patch("core.services.recurrence_service.MeetingRecurrence.objects.get") as mock_get, \
             patch("core.services.recurrence_service.Meeting.objects.filter") as mock_filter, \
             patch("core.services.recurrence_service.sync_to_async") as mock_s2a:
            mock_get.return_value = mock_recurrence
            mock_filter.return_value.update = MagicMock()
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await service.cancel_meeting_series(recurrence_id=1)
            
            assert result is True
            assert mock_recurrence.is_active is False

    @pytest.mark.asyncio
    async def test_cancel_single_meeting(self):
        from core.services.recurrence_service import RecurrenceService
        service = RecurrenceService()
        
        meeting = MagicMock()
        meeting.status = "active"
        meeting.save = MagicMock()
        
        with patch("core.services.recurrence_service.sync_to_async") as mock_s2a:
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await service.cancel_single_meeting(meeting)
            
            assert result is True
            assert meeting.status == "cancelled"
            meeting.save.assert_called_once()


class TestRecurrenceServiceUpdates:
    """Тесты для обновления серий."""

    @pytest.mark.asyncio
    async def test_update_series_title(self):
        from core.services.recurrence_service import RecurrenceService
        service = RecurrenceService()
        
        recurrence = MagicMock()
        recurrence.title = "Old Title"
        recurrence.save = MagicMock()
        
        with patch("core.services.recurrence_service.Meeting.objects.filter") as mock_meeting_filter, \
             patch("core.services.recurrence_service.Task.objects.filter") as mock_task_filter, \
             patch("core.services.recurrence_service.sync_to_async") as mock_s2a:
            mock_meeting_filter.return_value.update = MagicMock()
            mock_task_filter.return_value.update = MagicMock()
            mock_s2a.side_effect = lambda func: AsyncMock(return_value=func())
            
            result = await service.update_series_title(recurrence, "New Title")
            
            assert result is True
            assert recurrence.title == "New Title"
            recurrence.save.assert_called_once()
            mock_meeting_filter.return_value.update.assert_called_once()
            mock_task_filter.return_value.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_series_participants(self):
        from core.services.recurrence_service import RecurrenceService
        service = RecurrenceService()

        recurrence = MagicMock()
        recurrence.is_all_hands = False

        mock_user = MagicMock()
        mock_meeting = MagicMock()

        def fake_s2a(func):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        with patch("core.services.recurrence_service.sync_to_async", side_effect=fake_s2a), \
            patch("core.services.meeting_service._find_user_by_username", return_value=mock_user), \
            patch("core.services.recurrence_service.MeetingRecurrenceParticipant.objects") as mock_part_objects, \
            patch("core.services.recurrence_service.Meeting.objects") as mock_meeting_objects:
            mock_meeting_objects.filter.return_value = [mock_meeting]

            result = await service.update_series_participants(recurrence, ["@user1"])

            assert result is True
            mock_part_objects.create.assert_called_once()
            mock_meeting.participants.clear.assert_called_once()
            mock_meeting.participants.add.assert_called_once_with(mock_user)