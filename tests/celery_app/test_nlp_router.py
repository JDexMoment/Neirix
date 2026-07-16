"""
Тесты для NLP-роутера.
Все вызовы GigaChat замоканы.
"""
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from django.utils import timezone
from asgiref.sync import sync_to_async


# ══════════════════════════════════════════════════════════════════
# ТЕСТЫ: detect_intent (LLMClient импортируется из core.utils.llm_client)
# ══════════════════════════════════════════════════════════════════

class TestDetectIntent:

    @pytest.mark.asyncio
    async def test_show_tasks_tomorrow(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "show_tasks", "filter": "tomorrow"}')
            MockLLM.return_value = mock
            result = await detect_intent("какие задачи на завтра?")
        assert result is not None
        assert result["intent"] == "show_tasks"
        assert result["filter"] == "tomorrow"

    @pytest.mark.asyncio
    async def test_reschedule_meeting(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "reschedule_meeting", "title": "пивзавод", "new_date": "2026-07-18"}')
            MockLLM.return_value = mock
            result = await detect_intent("перенеси встречу")
        assert result is not None
        assert result["intent"] == "reschedule_meeting"

    @pytest.mark.asyncio
    async def test_edit_task_title(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "edit_task", "title": "задача", "new_title": "новое"}')
            MockLLM.return_value = mock
            result = await detect_intent("измени название задачи")
        assert result is not None
        assert result["intent"] == "edit_task"
        assert result["new_title"] == "новое"

    @pytest.mark.asyncio
    async def test_create_task(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "create_task", "title": "купить молоко", "username": "ivanov"}')
            MockLLM.return_value = mock
            result = await detect_intent("создай задачу")
        assert result is not None
        assert result["intent"] == "create_task"
        assert result["username"] == "ivanov"

    @pytest.mark.asyncio
    async def test_create_meeting(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "create_meeting", "title": "совещание"}')
            MockLLM.return_value = mock
            result = await detect_intent("назначь встречу")
        assert result is not None
        assert result["intent"] == "create_meeting"

    @pytest.mark.asyncio
    async def test_unknown_returns_none(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "unknown"}')
            MockLLM.return_value = mock
            result = await detect_intent("привет")
        assert result is None

    @pytest.mark.asyncio
    async def test_edit_meeting_title(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "edit_meeting", "title": "пивзавод", "new_title": "экскурсия"}')
            MockLLM.return_value = mock
            result = await detect_intent("измени название встречи")
        assert result is not None
        assert result["intent"] == "edit_meeting"
        assert result["new_title"] == "экскурсия"

    @pytest.mark.asyncio
    async def test_cancel_meeting(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "cancel_meeting", "title": "идет в тюрьму"}')
            MockLLM.return_value = mock
            result = await detect_intent("отмени встречу")
        assert result is not None
        assert result["intent"] == "cancel_meeting"

    @pytest.mark.asyncio
    async def test_show_summary(self):
        from core.services.nlp_router import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "show_summary", "date": "2026-07-15"}')
            MockLLM.return_value = mock
            result = await detect_intent("что было вчера?")
        assert result is not None
        assert result["intent"] == "show_summary"


# ══════════════════════════════════════════════════════════════════
# ТЕСТЫ: handle_nlp_command
# ══════════════════════════════════════════════════════════════════

class TestHandleNlpCommand:

    @pytest.mark.asyncio
    async def test_show_tasks(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        with patch("bot.handlers.tasks._handle_nlp_query", new=AsyncMock()) as mock_nlp:
            result = await handle_nlp_command(mock_msg, {"intent": "show_tasks"})
        assert result is True
        mock_nlp.assert_called_once()

    @pytest.mark.asyncio
    async def test_show_meetings(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        with patch("bot.handlers.meetings._handle_nlp_meeting_query", new=AsyncMock()) as mock_nlp:
            result = await handle_nlp_command(mock_msg, {"intent": "show_meetings"})
        assert result is True
        mock_nlp.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_meeting_no_title(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        with patch("core.services.nlp_router._find_meetings", return_value=[]):
            result = await handle_nlp_command(mock_msg, {"intent": "cancel_meeting"})
        assert result is True
        mock_msg.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_reschedule_meeting_parses_date(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        with patch("core.services.nlp_router._find_meetings", return_value=[MagicMock(id=1, title="Test")]):
            with patch("core.services.meeting_service.MeetingService") as MockMS:
                mock_ms = MagicMock()
                mock_ms.reschedule_meeting = AsyncMock(return_value=MagicMock())
                MockMS.return_value = mock_ms
                with patch("core.services.nlp_router._parse_nlp_date", return_value=timezone.now() + timedelta(hours=2)):
                    result = await handle_nlp_command(mock_msg, {"intent": "reschedule_meeting", "title": "Test", "new_date": "2026-07-20"})
        assert result is True

    @pytest.mark.asyncio
    async def test_edit_task_changes_title(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        mock_task = MagicMock()
        mock_task.id = 1
        mock_task.title = "Старое"
        mock_task.status = "open"
        with patch("core.services.nlp_router._find_tasks", return_value=[mock_task]):
            with patch("core.services.task_service.TaskService") as MockTS:
                mock_ts = MagicMock()
                mock_ts.update_title = AsyncMock(return_value=True)
                mock_ts.get_task_by_id = AsyncMock(return_value=mock_task)
                MockTS.return_value = mock_ts
                result = await handle_nlp_command(mock_msg, {"intent": "edit_task", "title": "Старое", "new_title": "Новое"})
        assert result is True
        mock_ts.update_title.assert_called_once_with(1, "Новое")

    @pytest.mark.asyncio
    async def test_edit_task_changes_assignee(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        with patch("core.services.nlp_router._find_tasks", return_value=[MagicMock(id=1, title="Задача", status="open")]):
            with patch("core.services.task_service.TaskService") as MockTS:
                mock_ts = MagicMock()
                mock_ts.update_assignees = AsyncMock(return_value=True)
                mock_ts.get_task_by_id = AsyncMock(return_value=MagicMock(id=1, title="Задача"))
                MockTS.return_value = mock_ts
                # _format_assignees импортируется из bot.handlers.tasks внутри nlp_router
                with patch("bot.handlers.tasks._format_assignees", return_value="@ivanov"):
                    result = await handle_nlp_command(mock_msg, {"intent": "edit_task", "title": "Задача", "new_assignee": "petrov"})
        assert result is True
        mock_ts.update_assignees.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_meeting_changes_participants(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        mock_meeting = MagicMock()
        mock_meeting.id = 1
        mock_meeting.title = "Встреча"
        mock_meeting.status = "active"
        mock_meeting.start_at = timezone.now() + timedelta(days=1)
        with patch("core.services.nlp_router._find_meetings", return_value=[mock_meeting]):
            with patch("core.services.meeting_service.MeetingService") as MockMS:
                mock_ms = MagicMock()
                mock_ms.update_participants = AsyncMock(return_value=True)
                mock_ms.get_meeting_by_id = AsyncMock(return_value=mock_meeting)
                MockMS.return_value = mock_ms
                # _format_participants импортируется из bot.handlers.meetings
                with patch("bot.handlers.meetings._format_participants", return_value="@ivanov"):
                    result = await handle_nlp_command(mock_msg, {"intent": "edit_meeting", "title": "Встреча", "new_assignee": "ivanov"})
        assert result is True
        mock_ms.update_participants.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_create_task_creates_real_task(self):
        from core.services.nlp_router import handle_nlp_command
        from core.models import TelegramChat, TelegramUser, Topic
        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-1100, title="Test", type="supergroup")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(telegram_id=100, username="tester", full_name="Tester")
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock(return_value=None)
        mock_msg.message_id = 999
        mock_msg.date = timezone.now()
        mock_msg.text = "создай задачу"
        mock_msg.from_user = MagicMock(id=100, username="tester", full_name="Tester", is_bot=False)
        mock_msg.chat = MagicMock(id=100100, type="private", title="", is_forum=False)
        with patch("core.services.nlp_router._get_ctx", return_value=(chat, topic, user)):
            result = await handle_nlp_command(mock_msg, {"intent": "create_task", "title": "отчёт", "username": "ivanov"})
        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_create_meeting_creates_real_meeting(self):
        from core.services.nlp_router import handle_nlp_command
        from core.models import TelegramChat, TelegramUser, Topic
        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-1110, title="Test", type="supergroup")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(telegram_id=110, username="boss", full_name="Boss")
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock(return_value=None)
        mock_msg.message_id = 1000
        mock_msg.date = timezone.now()
        mock_msg.text = "назначь встречу"
        mock_msg.from_user = MagicMock(id=110, username="boss", full_name="Boss", is_bot=False)
        mock_msg.chat = MagicMock(id=100200, type="private", title="", is_forum=False)
        with patch("core.services.nlp_router._get_ctx", return_value=(chat, topic, user)):
            result = await handle_nlp_command(mock_msg, {"intent": "create_meeting", "title": "совещание", "new_date": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d"), "new_time": "14:00"})
        assert result is True

    @pytest.mark.asyncio
    async def test_unknown_intent_returns_false(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        result = await handle_nlp_command(mock_msg, {"intent": "nonexistent"})
        assert result is False

    @pytest.mark.asyncio
    async def test_error_inside_handler_returns_true(self):
        from core.services.nlp_router import handle_nlp_command
        mock_msg = MagicMock(spec=['answer'])
        mock_msg.answer = AsyncMock()
        result = await handle_nlp_command(mock_msg, {"intent": "reschedule_meeting"})
        assert result is True


# ══════════════════════════════════════════════════════════════════
# ТЕСТЫ: _find_tasks, _find_meetings, _parse_nlp_date
# ══════════════════════════════════════════════════════════════════

class TestFindTasks:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_find_tasks_exact_match(self):
        from core.models import TelegramChat, TelegramUser, Topic, Task, Message
        from core.services.nlp_router import _find_tasks
        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-1000, title="Test", type="supergroup")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(telegram_id=1, username="ivanov", full_name="Ivan")
        msg = await sync_to_async(Message.objects.create)(telegram_msg_id=1, chat=chat, topic=topic, author=user, text="test", timestamp=timezone.now())
        t = await sync_to_async(Task.objects.create)(title="сделать отчёт", topic=topic, status="open", creator=user, source_message=msg)
        mock_msg = MagicMock()
        with patch("core.services.nlp_router._get_ctx", return_value=(chat, topic, user)):
            tasks = await _find_tasks(mock_msg, "отчёт")
        assert len(tasks) == 1
        assert tasks[0].id == t.id

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_find_tasks_no_match(self):
        from core.models import TelegramChat, TelegramUser, Topic, Task, Message
        from core.services.nlp_router import _find_tasks
        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-1002, title="Test", type="supergroup")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(telegram_id=3, username="s", full_name="S")
        msg = await sync_to_async(Message.objects.create)(telegram_msg_id=3, chat=chat, topic=topic, author=user, text="test", timestamp=timezone.now())
        await sync_to_async(Task.objects.create)(title="совсем другая тема", topic=topic, status="open", creator=user, source_message=msg)
        mock_msg = MagicMock()
        with patch("core.services.nlp_router._get_ctx", return_value=(chat, topic, user)):
            tasks = await _find_tasks(mock_msg, "нигде нет такого")
        assert tasks == []


class TestFindMeetings:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_find_meetings_exact(self):
        from core.models import TelegramChat, TelegramUser, Topic, Meeting, Message
        from core.services.nlp_router import _find_meetings
        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-1010, title="Test", type="supergroup")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(telegram_id=10, username="boss", full_name="Boss")
        msg = await sync_to_async(Message.objects.create)(telegram_msg_id=101, chat=chat, topic=topic, author=user, text="test", timestamp=timezone.now())
        m = await sync_to_async(Meeting.objects.create)(title="встреча на пивзаводе", topic=topic, start_at=timezone.now() + timedelta(days=1), creator=user, source_message=msg)
        mock_msg = MagicMock()
        with patch("core.services.nlp_router._get_ctx", return_value=(chat, topic, user)):
            result = await _find_meetings(mock_msg, "пивзавод")
        assert len(result) == 1
        assert result[0].id == m.id


class TestParseNlpDate:
    @pytest.mark.asyncio
    async def test_parse_date_only(self):
        from core.services.nlp_router import _parse_nlp_date
        r = await _parse_nlp_date(MagicMock(), {"new_date": "2026-07-18", "new_time": ""})
        assert r is not None
        assert r.strftime("%Y-%m-%d") == "2026-07-18"

    @pytest.mark.asyncio
    async def test_parse_date_time(self):
        from core.services.nlp_router import _parse_nlp_date
        r = await _parse_nlp_date(MagicMock(), {"new_date": "2026-07-18", "new_time": "14:00"})
        assert r is not None
        assert r.strftime("%Y-%m-%d %H:%M") == "2026-07-18 14:00"

    @pytest.mark.asyncio
    async def test_parse_no_date(self):
        from core.services.nlp_router import _parse_nlp_date
        r = await _parse_nlp_date(MagicMock(), {})
        assert r is None
