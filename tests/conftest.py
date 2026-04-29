import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, User, Message

@pytest.fixture
def mock_bot():
    bot = AsyncMock(spec=Bot)
    bot.send_message = AsyncMock()
    return bot

@pytest.fixture
def dispatcher(mock_bot):
    dp = Dispatcher(storage=MemoryStorage())
    dp["bot"] = mock_bot
    return dp

@pytest.fixture
def mock_db_utils():
    with patch("bot.handlers.chat_events.db_utils") as mock_chat_events_db, \
         patch("bot.handlers.chat_link.db_utils") as mock_chat_link_db, \
         patch("bot.handlers.meetings.db_utils") as mock_meetings_db:
        yield {
            "chat_events": mock_chat_events_db,
            "chat_link": mock_chat_link_db,
            "meetings": mock_meetings_db,
        }

@pytest.fixture
def private_chat():
    return Chat(id=111, type="private", title=None)

@pytest.fixture
def group_chat():
    return Chat(id=-123456, type="supergroup", title="Test Group", is_forum=False)

@pytest.fixture
def telegram_user():
    return User(id=999, is_bot=False, first_name="Test", username="testuser")