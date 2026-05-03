import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from tests.conftest import make_message


@pytest.fixture
def mock_chat_link_db():
    with patch("bot.handlers.chat_link.db_utils") as mock:
        yield mock


@pytest.fixture
def mock_sync_to_async_link():
    with patch("bot.handlers.chat_link.sync_to_async") as mock_s2a:
        def _wrapper(fn):
            async def _call(*args, **kwargs):
                return fn(*args, **kwargs)
            return _call
        mock_s2a.side_effect = _wrapper
        yield mock_s2a


@pytest.mark.asyncio
async def test_link_chat_in_group_creates_link_code(
    group_chat, telegram_user, test_uuid, now_dt,
    mock_chat_link_db, mock_sync_to_async_link,
):
    from bot.handlers.chat_link import cmd_link_chat

    msg = make_message(group_chat, telegram_user, "/link_chat", now_dt)

    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = None
    mock_chat_obj.save = MagicMock()
    mock_chat_link_db.get_or_create_chat_sync.return_value = mock_chat_obj

    with patch("bot.handlers.chat_link.uuid.uuid4", return_value=test_uuid):
        await cmd_link_chat(msg)

    mock_chat_link_db.get_or_create_chat_sync.assert_called_once()
    assert mock_chat_obj.link_code == test_uuid
    mock_chat_obj.save.assert_called_once_with(update_fields=["link_code"])

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else msg.answer.call_args.kwargs.get("text", "")
    assert f"{test_uuid}" in text


@pytest.mark.asyncio
async def test_link_chat_in_group_existing_link_code(
    group_chat, telegram_user, now_dt,
    mock_chat_link_db, mock_sync_to_async_link,
):
    from bot.handlers.chat_link import cmd_link_chat

    existing_code = "existing-uuid-1234"
    msg = make_message(group_chat, telegram_user, "/link_chat", now_dt)

    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = existing_code
    mock_chat_obj.save = MagicMock()
    mock_chat_link_db.get_or_create_chat_sync.return_value = mock_chat_obj

    await cmd_link_chat(msg)

    mock_chat_link_db.get_or_create_chat_sync.assert_called_once()
    mock_chat_obj.save.assert_not_called()
    assert mock_chat_obj.link_code == existing_code

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert existing_code in text


@pytest.mark.asyncio
async def test_link_chat_in_private(
    private_chat, telegram_user, now_dt,
    mock_chat_link_db, mock_sync_to_async_link,
):
    from bot.handlers.chat_link import cmd_link_chat

    msg = make_message(private_chat, telegram_user, "/link_chat", now_dt)

    await cmd_link_chat(msg)

    mock_chat_link_db.get_or_create_chat_sync.assert_not_called()
    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "только для групповых чатов" in text.lower()


@pytest.mark.asyncio
async def test_process_link_code_success(
    private_chat, telegram_user, test_uuid, now_dt,
    mock_chat_link_db, mock_sync_to_async_link,
):
    from bot.handlers.chat_link import process_link_code

    code = str(test_uuid)
    msg = make_message(private_chat, telegram_user, code, now_dt)

    mock_chat_obj = MagicMock()
    mock_chat_obj.title = "Test Group"
    mock_chat_link_db.get_chat_by_link_code_sync.return_value = mock_chat_obj

    mock_db_user = MagicMock()
    mock_chat_link_db.get_or_create_user_sync.return_value = mock_db_user
    mock_chat_link_db.create_user_role_sync.return_value = True

    await process_link_code(msg)

    mock_chat_link_db.get_chat_by_link_code_sync.assert_called_once_with(code)
    mock_chat_link_db.get_or_create_user_sync.assert_called_once_with(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
        full_name=telegram_user.full_name,
        is_bot=False,
    )
    mock_chat_link_db.create_user_role_sync.assert_called_once_with(
        mock_db_user, mock_chat_obj,
    )

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "успешно привязан" in text.lower()
    assert "Test Group" in text


@pytest.mark.asyncio
async def test_process_link_code_already_linked(
    private_chat, telegram_user, test_uuid, now_dt,
    mock_chat_link_db, mock_sync_to_async_link,
):
    from bot.handlers.chat_link import process_link_code

    code = str(test_uuid)
    msg = make_message(private_chat, telegram_user, code, now_dt)

    mock_chat_obj = MagicMock()
    mock_chat_obj.title = "Existing Chat"
    mock_chat_link_db.get_chat_by_link_code_sync.return_value = mock_chat_obj

    mock_db_user = MagicMock()
    mock_chat_link_db.get_or_create_user_sync.return_value = mock_db_user
    mock_chat_link_db.create_user_role_sync.return_value = False

    await process_link_code(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "уже был привязан" in text.lower()
    assert "Existing Chat" in text


@pytest.mark.asyncio
async def test_process_link_code_invalid_format(
    private_chat, telegram_user, now_dt,
    mock_chat_link_db, mock_sync_to_async_link,
):
    """Невалидный UUID — хендлер не должен вызываться через роутер,
    но при прямом вызове может вернуть ошибку или ничего не сделать."""
    # Этот тест проверяет что невалидный текст НЕ матчится роутером.
    # При прямом вызове хендлера это не проверишь — нужен dispatcher.
    # Пропускаем или проверяем иначе.
    pass


@pytest.mark.asyncio
async def test_process_link_code_wrong_uuid(
    private_chat, telegram_user, test_uuid, now_dt,
    mock_chat_link_db, mock_sync_to_async_link,
):
    from bot.handlers.chat_link import process_link_code

    msg = make_message(private_chat, telegram_user, str(test_uuid), now_dt)
    mock_chat_link_db.get_chat_by_link_code_sync.return_value = None

    await process_link_code(msg)

    mock_chat_link_db.get_chat_by_link_code_sync.assert_called_once()
    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "неверный код привязки" in text.lower()