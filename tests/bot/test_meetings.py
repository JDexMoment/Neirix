import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from django.utils import timezone
from aiogram import types
from bot.meetings import router

@pytest.fixture
def mock_meeting_queryset():
    meeting1 = MagicMock()
    meeting1.title = "Daily Standup"
    meeting1.start_at = timezone.now() + timezone.timedelta(hours=1)
    meeting1.participants.all.return_value = [MagicMock(username="user1", full_name="User One")]
    meeting2 = MagicMock()
    meeting2.title = "Sprint Review"
    meeting2.start_at = timezone.now() + timezone.timedelta(days=1)
    meeting2.participants.all.return_value = [MagicMock(username=None, full_name="User Two")]
    return [meeting1, meeting2]

@pytest.mark.asyncio
async def test_meetings_private_with_meetings(dispatcher, mock_bot, private_chat, telegram_user, mock_meeting_queryset):
    message = types.Message(
        message_id=1,
        chat=private_chat,
        from_user=telegram_user,
        date=1234567890,
        text="/meetings"
    )

    mock_chat = MagicMock()
    mock_topic = None
    mock_db_user = MagicMock()

    with patch("bot.meetings.get_chat_context", new=AsyncMock(return_value=(mock_chat, mock_topic, mock_db_user))):
        with patch("core.models.Meeting.objects.filter") as mock_filter:
            mock_filter.return_value.order_by.return_value.select_related.return_value.prefetch_related.return_value = mock_meeting_queryset

            dispatcher.include_router(router)
            await dispatcher.feed_update(bot=mock_bot, update=types.Update(update_id=1, message=message))

    mock_bot.send_message.assert_called_once()
    text = mock_bot.send_message.call_args.kwargs["text"]
    assert "📅 Ваши встречи" in text
    assert "Daily Standup" in text
    assert "Sprint Review" in text
    assert "@user1" in text
    assert "User Two" in text  # без @, т.к. username None

@pytest.mark.asyncio
async def test_meetings_private_no_meetings(dispatcher, mock_bot, private_chat, telegram_user):
    message = types.Message(
        message_id=1,
        chat=private_chat,
        from_user=telegram_user,
        date=1234567890,
        text="/meetings"
    )
    mock_chat = MagicMock()
    mock_topic = None
    mock_db_user = MagicMock()

    with patch("bot.meetings.get_chat_context", new=AsyncMock(return_value=(mock_chat, mock_topic, mock_db_user))):
        with patch("core.models.Meeting.objects.filter") as mock_filter:
            mock_filter.return_value.order_by.return_value.select_related.return_value.prefetch_related.return_value = []

            dispatcher.include_router(router)
            await dispatcher.feed_update(bot=mock_bot, update=types.Update(update_id=1, message=message))

    mock_bot.send_message.assert_called_once_with("Нет предстоящих встреч.")

@pytest.mark.asyncio
async def test_meetings_group_with_meetings(dispatcher, mock_bot, group_chat, telegram_user, mock_meeting_queryset):
    message = types.Message(
        message_id=1,
        chat=group_chat,
        from_user=telegram_user,
        date=1234567890,
        text="/meetings"
    )
    mock_chat = MagicMock()
    mock_chat.title = "Test Group"
    mock_topic = None
    mock_db_user = MagicMock()

    with patch("bot.meetings.get_chat_context", new=AsyncMock(return_value=(mock_chat, mock_topic, mock_db_user))):
        with patch("core.models.Meeting.objects.filter") as mock_filter:
            mock_filter.return_value.order_by.return_value.prefetch_related.return_value = mock_meeting_queryset

            dispatcher.include_router(router)
            await dispatcher.feed_update(bot=mock_bot, update=types.Update(update_id=1, message=message))

    mock_bot.send_message.assert_called_once()
    text = mock_bot.send_message.call_args.kwargs["text"]
    assert "📅 Встречи чата Test Group" in text