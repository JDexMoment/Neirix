# tests/bot/test_summary_hard.py
from unittest.mock import patch, MagicMock

# Самый первый патч — до загрузки любых модулей проекта
with patch('core.services.summary_service.VectorStoreClient', return_value=MagicMock()):
    import pytest
    from unittest.mock import AsyncMock, MagicMock, patch, call
    from datetime import datetime, timedelta
    from aiogram.types import Message, Chat, User
    from bot.handlers.summary import cmd_summary
    from core.models import Summary


@pytest.fixture
def message_mock():
    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Test", username="testuser")
    msg.chat = Chat(id=-123456, type="supergroup", title="Test Group")
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


# --- Жёсткие тесты ---

@pytest.mark.asyncio
async def test_long_summary_split(message_mock, mock_get_chat_context):
    message_mock.text = "/summary today"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    long_content = "X" * 5000
    summary = MagicMock(spec=Summary, content=long_content,
                        period_start=datetime.now(), period_end=datetime.now() + timedelta(days=1))

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        with patch("bot.handlers.summary.summary_service.generate_summary_for_period",
                   return_value=summary):
            await cmd_summary(message_mock)

    # Ожидаем минимум 3 вызова: 1 - "Генерирую", 2 - части длинного сообщения
    assert message_mock.answer.call_count >= 3
    # Проверяем, что все части вместе содержат исходный текст
    combined = "".join(
        call_args[0][0] for call_args in message_mock.answer.call_args_list
        if call_args[0][0].startswith("X")
    )
    assert combined == long_content


@pytest.mark.asyncio
async def test_invalid_date_format(message_mock, mock_get_chat_context):
    message_mock.text = "/summary 2025-01-01"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)
    await cmd_summary(message_mock)
    assert "Укажите начальную и конечную дату" in message_mock.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_date_order_no_crash(message_mock, mock_get_chat_context):
    message_mock.text = "/summary 2025-05-10 2025-01-01"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        with patch("bot.handlers.summary.summary_service.generate_summary_for_period",
                   return_value=MagicMock(spec=Summary, content="ok",
                                          period_start=datetime.now(), period_end=datetime.now())):
            await cmd_summary(message_mock)
    # Просто не должно упасть
    assert message_mock.answer.call_count >= 1


@pytest.mark.asyncio
async def test_topic_creation_failure(message_mock, mock_get_chat_context):
    message_mock.text = "/summary today"
    mock_get_chat_context.return_value = (message_mock.chat, None, message_mock.from_user)  # topic = None

    with patch("bot.handlers.summary.Topic.objects.get_or_create",
               side_effect=Exception("DB error")):
        try:
            await cmd_summary(message_mock)
        except Exception:
            pytest.fail("Хендлер не должен ронять исключение наружу")


@pytest.mark.asyncio
async def test_chat_is_none_return_early(message_mock, mock_get_chat_context):
    message_mock.text = "/summary today"
    mock_get_chat_context.return_value = (None, None, None)
    await cmd_summary(message_mock)
    message_mock.answer.assert_not_called()


@pytest.mark.asyncio
async def test_filter_parameters(message_mock, mock_get_chat_context, mock_sync_to_async):
    message_mock.text = "/summary 2025-03-10 2025-03-12"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        with patch("bot.handlers.summary.summary_service.generate_summary_for_period",
                   return_value=MagicMock(spec=Summary, content="ok",
                                          period_start=datetime(2025, 3, 10),
                                          period_end=datetime(2025, 3, 13))):
            await cmd_summary(message_mock)

        call_kwargs = mock_filter.call_args[1]
        assert call_kwargs['topic'] == topic
        assert call_kwargs['period_start__date'] == datetime(2025, 3, 10).date()
        assert call_kwargs['period_end__date'] == datetime(2025, 3, 13).date()


@pytest.mark.asyncio
async def test_generate_summary_arguments_yesterday(message_mock, mock_get_chat_context,
                                                    mock_summary_service):
    message_mock.text = "/summary yesterday"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        await cmd_summary(message_mock)

    mock_summary_service.generate_summary_for_period.assert_called_once()
    start_arg, end_arg = mock_summary_service.generate_summary_for_period.call_args[0][1:3]
    now = datetime.now()
    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    assert start_arg == yesterday_start
    assert end_arg == yesterday_start + timedelta(days=1)


@pytest.mark.asyncio
async def test_week_period_correct_range(message_mock, mock_get_chat_context,
                                         mock_summary_service):
    message_mock.text = "/summary week"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        await cmd_summary(message_mock)

    mock_summary_service.generate_summary_for_period.assert_called_once()
    start, end = mock_summary_service.generate_summary_for_period.call_args[0][1:3]
    today = datetime.now().date()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(weeks=1)
    expected_start = datetime.combine(last_monday, datetime.min.time())
    expected_end = expected_start + timedelta(days=7)
    assert start == expected_start
    assert end == expected_end