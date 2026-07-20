"""
Тесты для NLP-процессора (core/services/nlp_processor.py).
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
        from core.services.nlp_processor import detect_intent
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
        from core.services.nlp_processor import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "reschedule_meeting", "title": "пивзавод", "new_date": "2026-07-18"}')
            MockLLM.return_value = mock
            result = await detect_intent("перенеси встречу")
        assert result is not None
        assert result["intent"] == "reschedule_meeting"

    @pytest.mark.asyncio
    async def test_edit_task_title(self):
        from core.services.nlp_processor import detect_intent
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
        from core.services.nlp_processor import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "create_task", "title": "купить молоко", "new_assignee": "ivanov"}')
            MockLLM.return_value = mock
            result = await detect_intent("создай задачу")
        assert result is not None
        assert result["intent"] == "create_task"
        assert result["new_assignee"] == "ivanov"

    @pytest.mark.asyncio
    async def test_create_meeting(self):
        from core.services.nlp_processor import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "create_meeting", "title": "совещание"}')
            MockLLM.return_value = mock
            result = await detect_intent("назначь встречу")
        assert result is not None
        assert result["intent"] == "create_meeting"

    @pytest.mark.asyncio
    async def test_unknown_returns_none(self):
        from core.services.nlp_processor import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "unknown"}')
            MockLLM.return_value = mock
            result = await detect_intent("привет")
        assert result is None

    @pytest.mark.asyncio
    async def test_edit_meeting_title(self):
        from core.services.nlp_processor import detect_intent
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
        from core.services.nlp_processor import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "cancel_meeting", "title": "идет в тюрьму"}')
            MockLLM.return_value = mock
            result = await detect_intent("отмени встречу")
        assert result is not None
        assert result["intent"] == "cancel_meeting"

    @pytest.mark.asyncio
    async def test_show_summary(self):
        from core.services.nlp_processor import detect_intent
        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock = MagicMock()
            mock.chat_completion = AsyncMock(return_value='{"intent": "show_summary", "date": "2026-07-15"}')
            MockLLM.return_value = mock
            result = await detect_intent("что было вчера?")
        assert result is not None
        assert result["intent"] == "show_summary"


# ══════════════════════════════════════════════════════════════════
# ТЕСТЫ: process_nlp_message_standalone (через заглушки)
# ══════════════════════════════════════════════════════════════════

class TestProcessNlpMessage:

    @pytest.mark.asyncio
    async def test_return_false_when_no_intent(self):
        """Если detect_intent вернул None → process возвращает False."""
        from core.services.nlp_processor import process_nlp_message_standalone
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()

        with patch("core.services.nlp_processor.detect_intent", new=AsyncMock(return_value=None)):
            result = await process_nlp_message_standalone(
                bot=mock_bot,
                chat_id=123,
                user_telegram_id=456,
                text="привет",
                db_message_id=1,
                telegram_msg_id=100,
            )
        assert result is False
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_return_true_when_intent_found(self):
        """Если интент найден → process возвращает True."""
        from core.services.nlp_processor import process_nlp_message_standalone
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()

        with patch("core.services.nlp_processor.detect_intent", new=AsyncMock(return_value={"intent": "show_tasks", "filter": "all"})):
            result = await process_nlp_message_standalone(
                bot=mock_bot,
                chat_id=123,
                user_telegram_id=456,
                text="задачи",
                db_message_id=1,
                telegram_msg_id=100,
            )
        assert result is True
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_intent(self):
        """unknown intent → False."""
        from core.services.nlp_processor import process_nlp_message_standalone
        mock_bot = AsyncMock()

        with patch("core.services.nlp_processor.detect_intent", new=AsyncMock(return_value={"intent": "nonexistent"})):
            result = await process_nlp_message_standalone(
                bot=mock_bot,
                chat_id=123,
                user_telegram_id=456,
                text="что-то странное",
                db_message_id=1,
                telegram_msg_id=100,
            )
        assert result is False


# ══════════════════════════════════════════════════════════════════
# ТЕСТЫ: _find_tasks, _find_meetings, _parse_nlp_date
# ══════════════════════════════════════════════════════════════════

class TestFindTasks:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_find_tasks_exact_match(self):
        from core.services.nlp_processor import _find_tasks
        from core.models import TelegramChat, TelegramUser, Topic, Task, TaskAssignee, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-1000, title="TestChatForFind", type="supergroup")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(telegram_id=1, username="ivanov", full_name="Ivan")
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=1, chat=chat, topic=topic, author=user,
            text="test", timestamp=timezone.now(),
        )
        t = await sync_to_async(Task.objects.create)(
            title="сделать отчёт", topic=topic, status="open",
            creator=user, source_message=msg,
        )

        tasks = await _find_tasks(-1000, "отчёт")
        assert len(tasks) == 1
        assert tasks[0].id == t.id

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_find_tasks_no_match(self):
        from core.services.nlp_processor import _find_tasks
        from core.models import TelegramChat, TelegramUser, Topic, Task, Message

        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-1002, title="TestChatForFind2", type="supergroup")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(telegram_id=3, username="s", full_name="S")
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=3, chat=chat, topic=topic, author=user,
            text="test", timestamp=timezone.now(),
        )
        await sync_to_async(Task.objects.create)(
            title="совсем другая тема", topic=topic, status="open",
            creator=user, source_message=msg,
        )

        tasks = await _find_tasks(-1002, "нигде нет такого")
        assert tasks == []


class TestFindMeetings:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_find_meetings_exact(self):
        from core.services.nlp_processor import _find_meetings
        from core.models import TelegramChat, TelegramUser, Topic, Meeting, Message

        chat = await sync_to_async(TelegramChat.objects.create)(chat_id=-1010, title="TestMeetingChat", type="supergroup")
        topic = await sync_to_async(Topic.objects.create)(chat=chat, thread_id=0)
        user = await sync_to_async(TelegramUser.objects.create)(telegram_id=10, username="boss", full_name="Boss")
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=101, chat=chat, topic=topic, author=user,
            text="test", timestamp=timezone.now(),
        )
        m = await sync_to_async(Meeting.objects.create)(
            title="встреча на пивзаводе", topic=topic,
            start_at=timezone.now() + timedelta(days=1),
            creator=user, source_message=msg,
        )

        result = await _find_meetings(-1010, "пивзавод")
        assert len(result) == 1
        assert result[0].id == m.id


class TestParseNlpDate:
    @pytest.mark.asyncio
    async def test_parse_date_only(self):
        from core.services.nlp_processor import _parse_nlp_date
        r = await _parse_nlp_date("какой-то текст", {"new_date": "2026-07-18", "new_time": ""})
        assert r is not None
        assert r.strftime("%Y-%m-%d") == "2026-07-18"

    @pytest.mark.asyncio
    async def test_parse_date_time(self):
        from core.services.nlp_processor import _parse_nlp_date
        r = await _parse_nlp_date("какой-то текст", {"new_date": "2026-07-18", "new_time": "14:00"})
        assert r is not None
        assert r.strftime("%Y-%m-%d %H:%M") == "2026-07-18 14:00"

    @pytest.mark.asyncio
    async def test_parse_no_date(self):
        from core.services.nlp_processor import _parse_nlp_date
        r = await _parse_nlp_date("какой-то текст", {})
        assert r is None
