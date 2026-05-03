import pytest
from unittest.mock import MagicMock, patch

from aiogram import types
from bot.handlers.chat_events import router
from tests.conftest import include_router_once


def make_chat_member_updated(
    now_dt,
    chat_id=-123456,
    title="Test Group",
    chat_type="supergroup",
    is_forum=False,
    bot_id=8776202705,
):
    chat = types.Chat(id=chat_id, type=chat_type, title=title, is_forum=is_forum)
    actor = types.User(id=1, is_bot=False, first_name="Admin")
    bot_user = types.User(id=bot_id, is_bot=True, first_name="Neirix", username="Neirix1_bot")
    return types.ChatMemberUpdated(
        chat=chat,
        from_user=actor,
        date=now_dt,
        old_chat_member=types.ChatMemberLeft(user=bot_user),
        new_chat_member=types.ChatMemberMember(user=bot_user),
    )


@pytest.mark.asyncio
async def test_on_bot_added_creates_link_code(
    dispatcher, mock_bot, mock_db_utils, test_uuid, now_dt,
):
    event = make_chat_member_updated(now_dt=now_dt)

    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = None
    mock_chat_obj.save = MagicMock()
    mock_db_utils["chat_events"].get_or_create_chat_sync.return_value = mock_chat_obj

    include_router_once(dispatcher, router)

    with patch("bot.handlers.chat_events.uuid.uuid4", return_value=test_uuid):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=1, my_chat_member=event),
        )

    mock_db_utils["chat_events"].get_or_create_chat_sync.assert_called_once_with(
        chat_id=-123456, title="Test Group", chat_type="supergroup", is_forum=False,
    )
    assert mock_chat_obj.link_code == test_uuid
    mock_chat_obj.save.assert_called_once_with(update_fields=["link_code"])

    mock_bot.send_message.assert_called_once()
    kw = mock_bot.send_message.call_args.kwargs
    assert kw["chat_id"] == -123456
    assert "Привет! Я Neirix" in kw["text"]
    assert f"<code>{test_uuid}</code>" in kw["text"]
    assert kw["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_on_bot_added_existing_link_code(
    dispatcher, mock_bot, mock_db_utils, now_dt,
):
    existing_code = "existing-uuid-1234"
    event = make_chat_member_updated(now_dt=now_dt)

    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = existing_code
    mock_chat_obj.save = MagicMock()
    mock_db_utils["chat_events"].get_or_create_chat_sync.return_value = mock_chat_obj

    include_router_once(dispatcher, router)

    await dispatcher.feed_update(
        bot=mock_bot,
        update=types.Update(update_id=2, my_chat_member=event),
    )

    mock_chat_obj.save.assert_not_called()
    mock_bot.send_message.assert_called_once()
    kw = mock_bot.send_message.call_args.kwargs
    assert existing_code in kw["text"]


@pytest.mark.asyncio
async def test_on_bot_added_forum(
    dispatcher, mock_bot, mock_db_utils, test_uuid, now_dt,
):
    event = make_chat_member_updated(now_dt=now_dt, is_forum=True)

    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = None
    mock_chat_obj.save = MagicMock()
    mock_db_utils["chat_events"].get_or_create_chat_sync.return_value = mock_chat_obj

    include_router_once(dispatcher, router)

    with patch("bot.handlers.chat_events.uuid.uuid4", return_value=test_uuid):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=3, my_chat_member=event),
        )

    mock_db_utils["chat_events"].get_or_create_chat_sync.assert_called_once_with(
        chat_id=-123456, title="Test Group", chat_type="supergroup", is_forum=True,
    )


@pytest.mark.asyncio
async def test_on_bot_added_title_none(
    dispatcher, mock_bot, mock_db_utils, test_uuid, now_dt,
):
    event = make_chat_member_updated(now_dt=now_dt, title=None)

    mock_chat_obj = MagicMock()
    mock_chat_obj.link_code = None
    mock_chat_obj.save = MagicMock()
    mock_db_utils["chat_events"].get_or_create_chat_sync.return_value = mock_chat_obj

    include_router_once(dispatcher, router)

    with patch("bot.handlers.chat_events.uuid.uuid4", return_value=test_uuid):
        await dispatcher.feed_update(
            bot=mock_bot,
            update=types.Update(update_id=4, my_chat_member=event),
        )

    mock_db_utils["chat_events"].get_or_create_chat_sync.assert_called_once_with(
        chat_id=-123456, title="", chat_type="supergroup", is_forum=False,
    )