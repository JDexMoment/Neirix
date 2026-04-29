import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import types
from bot.handlers.chat_events import router


@pytest.fixture
def test_uuid():
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


def make_chat_member_updated(chat_id=-123456, title="Test Group", chat_type="supergroup", is_forum=False, bot_id=999):
    chat = types.Chat(id=chat_id, type=chat_type, title=title, is_forum=is_forum)
    user = types.User(id=1, is_bot=False, first_name="Test")
    event = types.ChatMemberUpdated(
        chat=chat,
        from_user=user,
        date=1234567890,
        old_chat_member=types.ChatMemberLeft(user=types.User(id=bot_id)),
        new_chat_member=types.ChatMemberMember(user=types.User(id=bot_id)),
    )
    return event


@pytest.mark.asyncio
async def test_on_bot_added_to_chat_creates_link_code(dispatcher, mock_bot, mock_db_utils, test_uuid):
    event = make_chat_member_updated()
    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = None
    mock_chat_obj.save = MagicMock()
    mock_db_utils["chat_events"].get_or_create_chat_sync.return_value = mock_chat_obj

    dispatcher.include_router(router)

    with patch("bot.chat_events.uuid.uuid4", return_value=test_uuid):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=1, my_chat_member=event),
        )

    mock_db_utils["chat_events"].get_or_create_chat_sync.assert_called_once_with(
        chat_id=-123456,
        title="Test Group",
        chat_type="supergroup",
        is_forum=False,
    )
    assert mock_chat_obj.link_code == test_uuid
    mock_chat_obj.save.assert_called_once_with(update_fields=["link_code"])

    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == -123456
    assert "Привет! Я Neirix" in call_kwargs["text"], "Имя бота написано с ошибкой"
    assert f"<code>{test_uuid}</code>" in call_kwargs["text"]
    assert call_kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_on_bot_added_to_chat_existing_link_code(dispatcher, mock_bot, mock_db_utils):
    existing_code = "existing-uuid-1234"
    event = make_chat_member_updated()
    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = existing_code
    mock_chat_obj.save = MagicMock()
    mock_db_utils["chat_events"].get_or_create_chat_sync.return_value = mock_chat_obj

    dispatcher.include_router(router)

    await dispatcher.feed_update(
        bot=mock_bot,
        update=types.Update(update_id=2, my_chat_member=event),
    )

    mock_db_utils["chat_events"].get_or_create_chat_sync.assert_called_once()
    mock_chat_obj.save.assert_not_called()
    assert mock_chat_obj.link_code == existing_code

    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args.kwargs
    assert existing_code in call_kwargs["text"]


@pytest.mark.asyncio
async def test_on_bot_added_to_chat_with_forum(dispatcher, mock_bot, mock_db_utils, test_uuid):
    event = make_chat_member_updated(is_forum=True)
    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = None
    mock_db_utils["chat_events"].get_or_create_chat_sync.return_value = mock_chat_obj

    dispatcher.include_router(router)

    with patch("bot.chat_events.uuid.uuid4", return_value=test_uuid):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=3, my_chat_member=event),
        )

    mock_db_utils["chat_events"].get_or_create_chat_sync.assert_called_once_with(
        chat_id=-123456,
        title="Test Group",
        chat_type="supergroup",
        is_forum=True,
    )


@pytest.mark.asyncio
async def test_on_bot_added_to_chat_title_none(dispatcher, mock_bot, mock_db_utils, test_uuid):
    event = make_chat_member_updated(title=None)
    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = None
    mock_db_utils["chat_events"].get_or_create_chat_sync.return_value = mock_chat_obj

    dispatcher.include_router(router)

    with patch("bot.chat_events.uuid.uuid4", return_value=test_uuid):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=4, my_chat_member=event),
        )

    mock_db_utils["chat_events"].get_or_create_chat_sync.assert_called_once_with(
        chat_id=-123456,
        title="",
        chat_type="supergroup",
        is_forum=False,
    )