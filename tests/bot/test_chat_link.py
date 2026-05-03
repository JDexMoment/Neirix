import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import types
from bot.handlers.chat_link import router


@pytest.fixture
def test_uuid():
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.mark.asyncio
async def test_link_chat_in_group_creates_link_code(
    dispatcher, mock_bot, mock_db_utils, group_chat, telegram_user, test_uuid
):
    """Команда в группе: создаёт link_code, сохраняет и отправляет сообщение."""
    message = types.Message(
        message_id=1,
        chat=group_chat,
        from_user=telegram_user,
        date=1234567890,
        text="/link_chat",
    )
    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = None
    mock_db_utils["chat_link"].get_or_create_chat_sync.return_value = mock_chat_obj

    dispatcher.include_router(router)

    with patch("bot.handlers.chat_link.uuid.uuid4", return_value=test_uuid), \
         patch("bot.handlers.chat_link.sync_to_async", side_effect=lambda f: f):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=1, message=message),
        )

    mock_db_utils["chat_link"].get_or_create_chat_sync.assert_called_once()
    assert mock_chat_obj.link_code == test_uuid
    mock_chat_obj.save.assert_called_once_with(update_fields=["link_code"])

    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == group_chat.id
    assert f"<code>{test_uuid}</code>" in call_kwargs["text"]
    assert call_kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_link_chat_in_group_existing_link_code(
    dispatcher, mock_bot, mock_db_utils, group_chat, telegram_user
):
    """Если у чата уже есть link_code, save не вызывается, выводится старый код."""
    existing_code = "existing-uuid-1234"
    message = types.Message(
        message_id=2,
        chat=group_chat,
        from_user=telegram_user,
        date=1234567890,
        text="/link_chat",
    )
    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = existing_code
    mock_db_utils["chat_link"].get_or_create_chat_sync.return_value = mock_chat_obj

    dispatcher.include_router(router)

    with patch("bot.handlers.chat_link.sync_to_async", side_effect=lambda f: f):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=2, message=message),
        )

    mock_db_utils["chat_link"].get_or_create_chat_sync.assert_called_once()
    mock_chat_obj.save.assert_not_called()
    assert mock_chat_obj.link_code == existing_code

    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert existing_code in call_kwargs["text"]
    assert "Код привязки чата" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_link_chat_in_private(
    dispatcher, mock_bot, mock_db_utils, private_chat, telegram_user
):
    """В личных сообщениях команда не работает, отправляется предупреждение."""
    message = types.Message(
        message_id=1,
        chat=private_chat,
        from_user=telegram_user,
        date=1234567890,
        text="/link_chat",
    )

    dispatcher.include_router(router)

    with patch("bot.handlers.chat_link.sync_to_async", side_effect=lambda f: f):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=1, message=message),
        )

    mock_db_utils["chat_link"].get_or_create_chat_sync.assert_not_called()
    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert "только для групповых чатов" in call_kwargs["text"]
    assert call_kwargs["chat_id"] == private_chat.id


@pytest.mark.asyncio
async def test_process_link_code_success(
    dispatcher, mock_bot, mock_db_utils, private_chat, telegram_user, test_uuid
):
    """Корректный UUID в личке — успешная привязка."""
    code = str(test_uuid)
    message = types.Message(
        message_id=1,
        chat=private_chat,
        from_user=telegram_user,
        date=1234567890,
        text=code,
    )

    mock_chat_obj = MagicMock()
    mock_chat_obj.title = "Test Group"
    mock_db_utils["chat_link"].get_chat_by_link_code_sync.return_value = mock_chat_obj
    mock_db_user = MagicMock()
    mock_db_utils["chat_link"].get_or_create_user_sync.return_value = mock_db_user
    mock_db_utils["chat_link"].create_user_role_sync.return_value = True

    dispatcher.include_router(router)

    with patch("bot.handlers.chat_link.sync_to_async", side_effect=lambda f: f):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=1, message=message),
        )

    mock_db_utils["chat_link"].get_chat_by_link_code_sync.assert_called_once_with(code)
    mock_db_utils["chat_link"].get_or_create_user_sync.assert_called_once_with(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
        full_name=telegram_user.full_name,
        is_bot=False,
    )
    mock_db_utils["chat_link"].create_user_role_sync.assert_called_once_with(
        mock_db_user, mock_chat_obj
    )
    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert "успешно привязан" in call_kwargs["text"]
    assert "Test Group" in call_kwargs["text"]
    assert call_kwargs["chat_id"] == private_chat.id
    assert call_kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_process_link_code_already_linked(
    dispatcher, mock_bot, mock_db_utils, private_chat, telegram_user, test_uuid
):
    """UUID корректен, но чат уже был привязан — сообщение об этом."""
    code = str(test_uuid)
    message = types.Message(
        message_id=1,
        chat=private_chat,
        from_user=telegram_user,
        date=1234567890,
        text=code,
    )

    mock_chat_obj = MagicMock()
    mock_chat_obj.title = "Existing Chat"
    mock_db_utils["chat_link"].get_chat_by_link_code_sync.return_value = mock_chat_obj
    mock_db_user = MagicMock()
    mock_db_utils["chat_link"].get_or_create_user_sync.return_value = mock_db_user
    mock_db_utils["chat_link"].create_user_role_sync.return_value = False

    dispatcher.include_router(router)

    with patch("bot.handlers.chat_link.sync_to_async", side_effect=lambda f: f):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=2, message=message),
        )

    mock_db_utils["chat_link"].create_user_role_sync.assert_called_once()
    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert "уже был привязан" in call_kwargs["text"]
    assert "Existing Chat" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_process_link_code_invalid_format(
    dispatcher, mock_bot, mock_db_utils, private_chat, telegram_user
):
    """Неверный формат кода — хендлер не срабатывает, сообщение не шлётся."""
    message = types.Message(
        message_id=1,
        chat=private_chat,
        from_user=telegram_user,
        date=1234567890,
        text="invalid-code",
    )

    dispatcher.include_router(router)

    await dispatcher.feed_update(
        bot=mock_bot,
        update=types.Update(update_id=1, message=message),
    )

    mock_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_process_link_code_wrong_uuid(
    dispatcher, mock_bot, mock_db_utils, private_chat, telegram_user, test_uuid
):
    """UUID валиден, но не найден в БД — бот сообщает об ошибке."""
    message = types.Message(
        message_id=1,
        chat=private_chat,
        from_user=telegram_user,
        date=1234567890,
        text=str(test_uuid),
    )

    mock_db_utils["chat_link"].get_chat_by_link_code_sync.return_value = None

    dispatcher.include_router(router)

    with patch("bot.handlers.chat_link.sync_to_async", side_effect=lambda f: f):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=3, message=message),
        )

    mock_db_utils["chat_link"].get_chat_by_link_code_sync.assert_called_once()
    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert "Неверный код привязки" in call_kwargs["text"]