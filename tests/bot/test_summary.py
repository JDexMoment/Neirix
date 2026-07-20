
from unittest.mock import patch, MagicMock

with patch('core.services.summary_service.VectorStoreClient') as mock_vsc:
    mock_vsc.return_value = MagicMock()
    from bot.handlers.summary import cmd_summary

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
from aiogram.types import Message, Chat, User
from core.models import Summary
from django.utils import timezone as dj_timezone


@pytest.fixture
def message_mock():
    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Test")
    msg.chat = Chat(id=-123456, type="supergroup")
    msg.answer = AsyncMock()
    return msg


@pytest.fixture
def mock_get_chat_context():
    with patch("bot.handlers.summary.get_chat_context") as mock:
        yield mock


@pytest.fixture
def mock_summary_service():
    with patch("bot.handlers.summary.summary_service") as mock:
        mock.generate_summary_for_period = AsyncMock()
        yield mock


@pytest.fixture
def mock_sync_to_async():
    with patch("bot.handlers.summary.sync_to_async") as mock:
        def _wrapper(fn):
            async def _call(*args, **kwargs):
                return fn(*args, **kwargs)
            return _call
        mock.side_effect = _wrapper
        yield mock


@pytest.fixture
def mock_default_topic():
    """
    Патчим _get_or_create_default_topic —
    чтобы не дёргать Django ORM.
    """
    with patch("bot.handlers.summary._get_or_create_default_topic") as mock:
        topic = MagicMock(id=1)
        mock.return_value = (topic, False)
        yield mock, topic

def _texts(msg):
    return " ".join(
        c.args[0] if c.args else c.kwargs.get("text", "")
        for c in msg.answer.call_args_list
    )


def _get_answer_texts(msg_mock) -> str:
    texts = []
    for call in msg_mock.answer.call_args_list:
        if call.args:
            texts.append(str(call.args[0]))
        elif "text" in call.kwargs:
            texts.append(str(call.kwargs["text"]))
    return " ".join(texts)


def _make_mock_summary(content="Тестовое саммари", days_ago=0):
    s = MagicMock()
    s.content = content
    s.period_start = datetime.now() - timedelta(days=days_ago)
    s.period_end = s.period_start + timedelta(days=1)
    return s


# ─────────────────────────────────────────────────────────────────────
# Smoke-тесты
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_no_args(message_mock, mock_get_chat_context):
    message_mock.text = "/summary"
    mock_get_chat_context.return_value = (message_mock.chat, None, message_mock.from_user)

    await cmd_summary(message_mock)

    message_mock.answer.assert_called_once()
    assert "Используйте:" in message_mock.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_summary_today_new(message_mock, mock_get_chat_context, mock_summary_service,
                                 mock_sync_to_async):
    message_mock.text = "/summary today"
    topic = MagicMock()  # замоканный объект Topic
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        summary = MagicMock(spec=Summary, content="Тестовое саммари за сегодня",
                            period_start=dj_timezone.now(), period_end=dj_timezone.now() + timedelta(days=1))
        mock_summary_service.generate_summary_for_period.return_value = summary

        await cmd_summary(message_mock)

        assert message_mock.answer.call_count == 2
        assert "Генерирую саммари" in message_mock.answer.call_args_list[0].args[0]
        assert "Тестовое саммари за сегодня" in message_mock.answer.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_summary_existing(message_mock, mock_get_chat_context, mock_summary_service):
    message_mock.text = "/summary yesterday"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    existing_summary = MagicMock(spec=Summary, content="Существующее саммари",
                                 period_start=dj_timezone.now() - timedelta(days=1),
                                 period_end=dj_timezone.now())
    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = existing_summary

        await cmd_summary(message_mock)
        mock_summary_service.generate_summary_for_period.assert_not_called()
        message_mock.answer.assert_called_once()
        assert "Существующее саммари" in message_mock.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_summary_with_dates(message_mock, mock_get_chat_context, mock_summary_service):
    message_mock.text = "/summary 2025-01-01 2025-01-03"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        summary = MagicMock(spec=Summary, content="Саммари за даты",
                            period_start=datetime(2025, 1, 1), period_end=datetime(2025, 1, 4))
        mock_summary_service.generate_summary_for_period.return_value = summary

        await cmd_summary(message_mock)

        assert message_mock.answer.call_count == 2
        assert "Саммари за даты" in message_mock.answer.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_summary_no_messages(message_mock, mock_get_chat_context, mock_summary_service):
    message_mock.text = "/summary week"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        mock_summary_service.generate_summary_for_period.return_value = None

        await cmd_summary(message_mock)

        assert "Не удалось сгенерировать саммари" in message_mock.answer.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_summary_generation_error(message_mock, mock_get_chat_context, mock_summary_service):
    message_mock.text = "/summary today"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        mock_summary_service.generate_summary_for_period.side_effect = Exception("API error")

        await cmd_summary(message_mock)

        assert "Ошибка при генерации саммари" in message_mock.answer.call_args_list[-1].args[0]
    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Хендлер не должен пробрасывать исключение наружу")

    text = _get_answer_texts(summary_message).lower()
    assert "ошибка" in text or "не удалось" in text


# ─────────────────────────────────────────────────────────────────────
# Даты
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_with_two_dates(
    summary_message,
    mock_get_chat_context_summary,
    mock_summary_service,
    mock_sync_summary,
    mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary 2025-01-01 2025-01-03"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = _make_mock_summary(
        "Саммари за даты"
    )

    await cmd_summary(summary_message)

    text = _get_answer_texts(summary_message)
    assert "Саммари за даты" in text


@pytest.mark.asyncio
async def test_summary_single_date_shows_help(
    summary_message,
    mock_get_chat_context_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary 2025-01-01"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    await cmd_summary(summary_message)

    text = _get_answer_texts(summary_message).lower()
    assert "дату" in text or "используйте" in text


@pytest.mark.asyncio
async def test_summary_reversed_dates_no_crash(
    summary_message,
    mock_get_chat_context_summary,
    mock_summary_service,
    mock_sync_summary,
    mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary 2025-05-10 2025-01-01"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = _make_mock_summary("ok")

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Хендлер не должен падать при обратном порядке дат")

    assert summary_message.answer.call_count >= 1


@pytest.mark.asyncio
async def test_summary_invalid_date_format(
    summary_message,
    mock_get_chat_context_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary abc def"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    await cmd_summary(summary_message)

    text = _get_answer_texts(summary_message).lower()
    assert "формат" in text or "yyyy" in text


# ─────────────────────────────────────────────────────────────────────
# Жёсткие тесты
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_long_content_no_crash(
    summary_message,
    mock_get_chat_context_summary,
    mock_summary_service,
    mock_sync_summary,
    mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary today"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = _make_mock_summary(
        "X" * 10000
    )

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Хендлер не должен падать на длинном контенте")

    assert summary_message.answer.call_count >= 1
    text = _get_answer_texts(summary_message)
    assert "X" in text


@pytest.mark.asyncio
async def test_summary_yesterday_period(
    summary_message,
    mock_get_chat_context_summary,
    mock_summary_service,
    mock_sync_summary,
    mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary yesterday"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = None

    await cmd_summary(summary_message)

    if mock_summary_service.generate_summary_for_period.called:
        call_args = mock_summary_service.generate_summary_for_period.call_args
        args = call_args.args if call_args.args else ()

        if len(args) >= 3:
            start_arg = args[1]
            end_arg = args[2]
            yesterday = (datetime.now() - timedelta(days=1)).date()
            assert start_arg.date() == yesterday
            assert (end_arg - start_arg).days == 1


@pytest.mark.asyncio
async def test_summary_default_topic_created_when_no_topic(
    summary_message,
    mock_get_chat_context_summary,
    mock_summary_service,
    mock_sync_summary,
    mock_existing_summary,
    mock_default_topic,
):
    """Если topic=None, handler создаёт дефолтный topic через _get_or_create_default_topic."""
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary today"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat,
        None,
        summary_message.from_user,
    )

    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = _make_mock_summary("ok")

    mock_topic_fn, mock_topic_obj = mock_default_topic

    await cmd_summary(summary_message)

    mock_topic_fn.assert_called_once()

    if mock_summary_service.generate_summary_for_period.called:
        call_args = mock_summary_service.generate_summary_for_period.call_args.args
        assert call_args[0] is mock_topic_obj


@pytest.mark.asyncio
async def test_summary_week_period_is_last_week(
    summary_message,
    mock_get_chat_context_summary,
    mock_summary_service,
    mock_sync_summary,
    mock_existing_summary,
):
    """week → период должен быть прошлая неделя (пн-вс)."""
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary week"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = None

    await cmd_summary(summary_message)

    if mock_summary_service.generate_summary_for_period.called:
        call_args = mock_summary_service.generate_summary_for_period.call_args.args
        if len(call_args) >= 3:
            start = call_args[1]
            end = call_args[2]
            assert (end - start).days == 7
            assert start.weekday() == 0

# ──────────────────────────────────────────────────────────────────
# Жёсткие тесты
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_html_in_content(
    summary_message, mock_get_chat_context_summary,
    mock_summary_service, mock_sync_summary, mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary today"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, MagicMock(id=1), summary_message.from_user,
    )
    mock_existing_summary.return_value = None

    s = MagicMock()
    s.content = "<script>alert('xss')</script> & test <b>bold</b>"
    s.period_start = datetime.now()
    s.period_end = datetime.now() + timedelta(days=1)
    mock_summary_service.generate_summary_for_period.return_value = s

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("HTML-спецсимволы в саммари не должны ломать бота")


@pytest.mark.asyncio
async def test_summary_empty_content(
    summary_message, mock_get_chat_context_summary,
    mock_summary_service, mock_sync_summary, mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary today"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, MagicMock(id=1), summary_message.from_user,
    )
    mock_existing_summary.return_value = None

    s = MagicMock()
    s.content = ""
    s.period_start = datetime.now()
    s.period_end = datetime.now() + timedelta(days=1)
    mock_summary_service.generate_summary_for_period.return_value = s

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Пустое саммари не должно ломать бота")


@pytest.mark.asyncio
async def test_summary_unicode_content(
    summary_message, mock_get_chat_context_summary,
    mock_summary_service, mock_sync_summary, mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary today"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, MagicMock(id=1), summary_message.from_user,
    )
    mock_existing_summary.return_value = None

    s = MagicMock()
    s.content = "Обсуждение 🔥💯 эмодзи и кириллица Ёё Щщ"
    s.period_start = datetime.now()
    s.period_end = datetime.now() + timedelta(days=1)
    mock_summary_service.generate_summary_for_period.return_value = s

    await cmd_summary(summary_message)

    text = _get_answer_texts(summary_message)
    assert "🔥" in text
    assert "кириллица" in text


@pytest.mark.asyncio
async def test_summary_future_dates(
    summary_message, mock_get_chat_context_summary,
    mock_summary_service, mock_sync_summary, mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary 2030-01-01 2030-12-31"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, MagicMock(id=1), summary_message.from_user,
    )
    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = None

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Будущие даты не должны ломать бота")


@pytest.mark.asyncio
async def test_summary_same_start_end_date(
    summary_message, mock_get_chat_context_summary,
    mock_summary_service, mock_sync_summary, mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary 2025-06-01 2025-06-01"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, MagicMock(id=1), summary_message.from_user,
    )
    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = None

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Одинаковые даты не должны ломать бота")


@pytest.mark.asyncio
async def test_summary_very_old_dates(
    summary_message, mock_get_chat_context_summary,
    mock_summary_service, mock_sync_summary, mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary 2000-01-01 2000-01-02"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, MagicMock(id=1), summary_message.from_user,
    )
    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = None

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Старые даты не должны ломать бота")


@pytest.mark.asyncio
async def test_summary_three_args_ignored(
    summary_message, mock_get_chat_context_summary,
    mock_summary_service, mock_sync_summary, mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary 2025-01-01 2025-01-02 extra"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, MagicMock(id=1), summary_message.from_user,
    )
    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = None

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Лишние аргументы не должны ломать бота")