import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_message


def _make_mock_meeting(title, hours_ahead=1, participants=None):
    meeting = MagicMock()
    meeting.title = title
    meeting.start_at = datetime.now(dt_timezone.utc) + timedelta(hours=hours_ahead)
    meeting.participants.all.return_value = participants or []
    return meeting


def _make_participant(username=None, full_name="Unknown", user_id=1):
    p = MagicMock()
    p.id = user_id
    p.username = username
    p.full_name = full_name
    return p


@pytest.fixture
def meetings_with_participants():
    return [
        _make_mock_meeting("Daily Standup", 1, [_make_participant("user1", "User One")]),
        _make_mock_meeting("Sprint Review", 24, [_make_participant(None, "User Two")]),
    ]


@pytest.fixture
def meetings_no_participants():
    return [_make_mock_meeting("General Meeting", 2, [])]


@pytest.fixture
def mock_get_chat_context_meetings():
    with patch("bot.handlers.meetings.get_chat_context") as mock:
        yield mock


@pytest.fixture
def mock_sync_meetings():
    with patch("bot.handlers.meetings.sync_to_async") as mock_s2a:
        yield mock_s2a


def _setup_sync_mock(mock_s2a, return_value):
    """Настраивает sync_to_async мок чтобы он возвращал нужное значение."""
    async def fake_fetch(*a, **kw):
        return return_value
    mock_s2a.return_value = fake_fetch


@pytest.mark.asyncio
async def test_meetings_private_with_meetings(
    private_chat, telegram_user, now_dt,
    meetings_with_participants,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    msg = make_message(private_chat, telegram_user, "/meetings", now_dt)
    mock_chat = MagicMock()
    mock_db_user = MagicMock()
    mock_get_chat_context_meetings.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_meetings, meetings_with_participants)

    await cmd_meetings(msg)

    # 1 заголовок + 2 встречи (каждая отдельным сообщением с кнопками)
    assert msg.answer.call_count == 3
    texts = [
        call.args[0] if call.args else call.kwargs.get("text", "")
        for call in msg.answer.call_args_list
    ]

    assert "📅 Ваши встречи" in texts[0]
    assert "Daily Standup" in texts[1]
    assert "@user1" in texts[1]
    assert "Sprint Review" in texts[2]
    assert "User Two" in texts[2]


@pytest.mark.asyncio
async def test_meetings_private_no_meetings(
    private_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    msg = make_message(private_chat, telegram_user, "/meetings", now_dt)
    mock_chat = MagicMock()
    mock_db_user = MagicMock()
    mock_get_chat_context_meetings.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_meetings, [])

    await cmd_meetings(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "Нет предстоящих встреч" in text


@pytest.mark.asyncio
async def test_meetings_group_with_meetings(
    group_chat, telegram_user, now_dt,
    meetings_with_participants,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_chat = MagicMock()
    mock_chat.title = "Test Group"
    mock_db_user = MagicMock()
    mock_get_chat_context_meetings.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_meetings, meetings_with_participants)

    await cmd_meetings(msg)

    assert msg.answer.call_count == 3
    texts = [
        call.args[0] if call.args else call.kwargs.get("text", "")
        for call in msg.answer.call_args_list
    ]

    assert "📅 Встречи чата Test Group" in texts[0]
    assert "Daily Standup" in texts[1]
    assert "Sprint Review" in texts[2]


@pytest.mark.asyncio
async def test_meetings_group_no_meetings(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_chat = MagicMock()
    mock_chat.title = "Test Group"
    mock_db_user = MagicMock()
    mock_get_chat_context_meetings.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_meetings, [])

    await cmd_meetings(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "Нет предстоящих встреч" in text


@pytest.mark.asyncio
async def test_meetings_all_participants_shown(
    group_chat, telegram_user, now_dt,
    meetings_no_participants,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_chat = MagicMock()
    mock_chat.title = "Test Group"
    mock_db_user = MagicMock()
    mock_get_chat_context_meetings.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_meetings, meetings_no_participants)

    await cmd_meetings(msg)

    # 1 заголовок + 1 встреча
    assert msg.answer.call_count == 2
    texts = [
        call.args[0] if call.args else call.kwargs.get("text", "")
        for call in msg.answer.call_args_list
    ]

    assert "📅 Встречи чата Test Group" in texts[0]
    assert "General Meeting" in texts[1]
    assert "Все участники" in texts[1]


@pytest.mark.asyncio
async def test_meetings_no_user(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_get_chat_context_meetings.return_value = (MagicMock(), None, None)

    await cmd_meetings(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "Не удалось определить пользователя" in text


@pytest.mark.asyncio
async def test_meetings_no_chat(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_db_user = MagicMock()
    mock_get_chat_context_meetings.return_value = (None, None, mock_db_user)

    await cmd_meetings(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "Не удалось определить чат" in text