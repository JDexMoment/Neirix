import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, User


@pytest.fixture
def test_uuid():
    import uuid
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def mock_bot():
    bot = AsyncMock(spec=Bot)
    bot.id = 8776202705
    bot.token = "123456:TEST_TOKEN"
    bot.send_message = AsyncMock()
    return bot


@pytest.fixture
def dispatcher():
    dp = Dispatcher(storage=MemoryStorage())
    return dp


@pytest.fixture
def mock_db_utils():
    with patch("bot.handlers.chat_events.db_utils") as mock_chat_events_db, \
         patch("bot.handlers.chat_link.db_utils") as mock_chat_link_db:
        yield {
            "chat_events": mock_chat_events_db,
            "chat_link": mock_chat_link_db,
        }


@pytest.fixture
def private_chat():
    return Chat(id=111, type="private", first_name="TestPrivateChat")


@pytest.fixture
def group_chat():
    return Chat(id=-123456, type="supergroup", title="Test Group", is_forum=False)


@pytest.fixture
def telegram_user():
    return User(id=999, is_bot=False, first_name="Test", last_name="User", username="testuser")


@pytest.fixture
def now_dt():
    return datetime.now(dt_timezone.utc)


def make_message(chat, user, text, now_dt, message_id=1):
    """Создаёт AsyncMock сообщения с рабочим .answer()."""
    msg = AsyncMock()
    msg.chat = chat
    msg.from_user = user
    msg.date = now_dt
    msg.text = text
    msg.message_id = message_id
    msg.answer = AsyncMock()
    msg.reply = AsyncMock()
    return msg


def include_router_once(dp: Dispatcher, router: Router):
    if router.parent_router is dp:
        return
    if router.parent_router is not None:
        try:
            router.parent_router.sub_routers.remove(router)
        except (ValueError, AttributeError):
            pass
        router._parent_router = None
    dp.include_router(router)