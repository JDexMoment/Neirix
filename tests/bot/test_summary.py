
from unittest.mock import patch, MagicMock

# Патчим VectorStoreClient в том модуле, где он используется (core.services.summary_service).
# Это надо сделать ДО того, как будет импортирован bot.handlers.summary.
with patch('core.services.summary_service.VectorStoreClient') as mock_vsc:
    mock_vsc.return_value = MagicMock()
    from bot.handlers.summary import cmd_summary

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
from aiogram.types import Message, Chat, User
from core.models import Summary


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

    # Мок существующего саммари – None, значит, будет генерация
    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = None
        # Мок результата генерации
        summary = MagicMock(spec=Summary, content="Тестовое саммари за сегодня",
                            period_start=datetime.now(), period_end=datetime.now() + timedelta(days=1))
        mock_summary_service.generate_summary_for_period.return_value = summary

        await cmd_summary(message_mock)

        # Проверяем, что сказали "Генерирую" и затем отправили результат
        assert message_mock.answer.call_count == 2
        assert "Генерирую саммари" in message_mock.answer.call_args_list[0].args[0]
        assert "Тестовое саммари за сегодня" in message_mock.answer.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_summary_existing(message_mock, mock_get_chat_context, mock_summary_service):
    message_mock.text = "/summary yesterday"
    topic = MagicMock()
    mock_get_chat_context.return_value = (message_mock.chat, topic, message_mock.from_user)

    existing_summary = MagicMock(spec=Summary, content="Существующее саммари",
                                 period_start=datetime.now() - timedelta(days=1),
                                 period_end=datetime.now())
    with patch("bot.handlers.summary.Summary.objects.filter") as mock_filter:
        mock_filter.return_value.first.return_value = existing_summary

        await cmd_summary(message_mock)

        # generate_summary_for_period НЕ должен вызываться
        mock_summary_service.generate_summary_for_period.assert_not_called()
        # Ожидаем только один ответ с контентом
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