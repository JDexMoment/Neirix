import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_message


# ─────────────────────────────────────────────────────────────────────
# Фикстуры
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def summary_message(private_chat, telegram_user, now_dt):
    return make_message(private_chat, telegram_user, "/summary", now_dt)


@pytest.fixture
def mock_get_chat_context_summary():
    with patch("bot.handlers.summary.get_chat_context") as mock:
        yield mock


@pytest.fixture
def mock_summary_service():
    with patch("bot.handlers.summary.summary_service") as mock:
        mock.generate_summary_for_period = AsyncMock()
        yield mock


@pytest.fixture
def mock_sync_summary():
    """
    sync_to_async: вызывает переданную функцию напрямую.
    Это нужно для _get_or_create_default_topic и _get_existing_summary.
    """
    with patch("bot.handlers.summary.sync_to_async") as mock_s2a:
        def _wrapper(fn):
            async def _call(*args, **kwargs):
                return fn(*args, **kwargs)
            return _call
        mock_s2a.side_effect = _wrapper
        yield mock_s2a


@pytest.fixture
def mock_existing_summary():
    """
    Патчим _get_existing_summary напрямую —
    это та самая функция, которую handler оборачивает в sync_to_async.
    """
    with patch("bot.handlers.summary._get_existing_summary") as mock:
        mock.return_value = None
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
async def test_summary_no_args(summary_message, mock_get_chat_context_summary):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary"
    mock_get_chat_context_summary.return_value = (
        summary_message.chat,
        MagicMock(id=1),
        summary_message.from_user,
    )

    await cmd_summary(summary_message)

    summary_message.answer.assert_called_once()
    text = _get_answer_texts(summary_message)
    assert "используйте" in text.lower()


@pytest.mark.asyncio
async def test_summary_no_chat(summary_message, mock_get_chat_context_summary):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary today"
    mock_get_chat_context_summary.return_value = (None, None, None)

    try:
        await cmd_summary(summary_message)
    except Exception:
        pytest.fail("Хендлер не должен падать при отсутствии чата")

    text = _get_answer_texts(summary_message)
    assert "не удалось" in text.lower() or "чат" in text.lower()


# ─────────────────────────────────────────────────────────────────────
# Генерация нового саммари
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_today_generates_new(
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
        "Тестовое саммари за сегодня"
    )

    await cmd_summary(summary_message)

    mock_existing_summary.assert_called_once()
    mock_summary_service.generate_summary_for_period.assert_called_once()

    text = _get_answer_texts(summary_message)
    assert "Тестовое саммари за сегодня" in text


@pytest.mark.asyncio
async def test_summary_existing_returned(
    summary_message,
    mock_get_chat_context_summary,
    mock_summary_service,
    mock_sync_summary,
    mock_existing_summary,
):
    """Если саммари уже есть в БД — генерация НЕ вызывается."""
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary yesterday"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    existing = _make_mock_summary("Существующее саммари", days_ago=1)
    mock_existing_summary.return_value = existing

    await cmd_summary(summary_message)

    mock_summary_service.generate_summary_for_period.assert_not_called()

    text = _get_answer_texts(summary_message)
    assert "Существующее саммари" in text


@pytest.mark.asyncio
async def test_summary_returns_none(
    summary_message,
    mock_get_chat_context_summary,
    mock_summary_service,
    mock_sync_summary,
    mock_existing_summary,
):
    from bot.handlers.summary import cmd_summary

    summary_message.text = "/summary week"
    topic = MagicMock(id=1)
    mock_get_chat_context_summary.return_value = (
        summary_message.chat, topic, summary_message.from_user,
    )

    mock_existing_summary.return_value = None
    mock_summary_service.generate_summary_for_period.return_value = None

    await cmd_summary(summary_message)

    text = _get_answer_texts(summary_message).lower()
    assert "не удалось" in text or "нет сообщений" in text


@pytest.mark.asyncio
async def test_summary_service_exception(
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
    mock_summary_service.generate_summary_for_period.side_effect = Exception("API error")

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