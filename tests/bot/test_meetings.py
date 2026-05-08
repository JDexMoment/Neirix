import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from django.utils import timezone
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

# ──────────────────────────────────────────────────────────────────
# Жёсткие тесты
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_meeting_empty_title(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    meeting = _make_mock_meeting("", hours_ahead=1)
    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_get_chat_context_meetings.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_meetings, [meeting])

    await cmd_meetings(msg)

    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    combined = " ".join(texts)
    assert "Без названия" in combined


@pytest.mark.asyncio
async def test_meeting_html_in_title(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    meeting = _make_mock_meeting("<b>Injection</b> & <script>")
    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_get_chat_context_meetings.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_meetings, [meeting])

    try:
        await cmd_meetings(msg)
    except Exception:
        pytest.fail("HTML в title не должен ломать бота")


@pytest.mark.asyncio
async def test_meeting_many_participants(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    participants = [_make_participant(username=f"user{i}", user_id=i) for i in range(20)]
    meeting = _make_mock_meeting("Большая встреча", participants=participants)
    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_get_chat_context_meetings.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_meetings, [meeting])

    await cmd_meetings(msg)

    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    combined = " ".join(texts)
    assert "@user0" in combined
    assert "@user19" in combined


@pytest.mark.asyncio
async def test_meeting_participant_no_username_no_fullname(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    p = MagicMock()
    p.id = 42
    p.username = None
    p.full_name = None
    meeting = _make_mock_meeting("Тест", participants=[p])
    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_get_chat_context_meetings.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_meetings, [meeting])

    try:
        await cmd_meetings(msg)
    except Exception:
        pytest.fail("Участник без данных не должен ломать бота")


@pytest.mark.asyncio
async def test_meeting_50_meetings(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_meetings, mock_sync_meetings,
):
    from bot.handlers.meetings import cmd_meetings

    meetings = [_make_mock_meeting(f"Встреча {i}", hours_ahead=i) for i in range(1, 51)]
    msg = make_message(group_chat, telegram_user, "/meetings", now_dt)
    mock_get_chat_context_meetings.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_meetings, meetings)

    await cmd_meetings(msg)

    assert msg.answer.call_count == 51


@pytest.mark.asyncio
async def test_meeting_cancel_nonexistent():
    from bot.handlers.meetings import callback_meeting_cancel

    callback = AsyncMock()
    callback.data = "meeting_cancel:99999"
    callback.answer = AsyncMock()
    callback.message = AsyncMock()

    with patch("bot.handlers.meetings.meeting_service") as mock_service:
        mock_service.get_meeting_by_id = AsyncMock(return_value=None)
        await callback_meeting_cancel(callback)

    callback.answer.assert_called_once()
    assert "не найдена" in callback.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_meeting_cancel_invalid_id():
    from bot.handlers.meetings import callback_meeting_cancel

    callback = AsyncMock()
    callback.data = "meeting_cancel:abc"
    callback.answer = AsyncMock()

    await callback_meeting_cancel(callback)

    callback.answer.assert_called_once()
    assert "некорректн" in callback.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_reschedule_past_date(
    group_chat, telegram_user, now_dt,
):
    from bot.handlers.meetings import process_reschedule_datetime
    from aiogram.fsm.context import FSMContext

    msg = make_message(group_chat, telegram_user, "01.01.2020 10:00", now_dt)

    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={
        "reschedule_meeting_id": 1,
        "reschedule_meeting_title": "Test",
    })

    await process_reschedule_datetime(msg, state)

    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    combined = " ".join(texts).lower()
    assert "будущем" in combined or "прошл" in combined


@pytest.mark.asyncio
async def test_reschedule_invalid_format(
    group_chat, telegram_user, now_dt,
):
    from bot.handlers.meetings import process_reschedule_datetime
    from aiogram.fsm.context import FSMContext

    msg = make_message(group_chat, telegram_user, "какой-то текст", now_dt)

    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={
        "reschedule_meeting_id": 1,
        "reschedule_meeting_title": "Test",
    })

    await process_reschedule_datetime(msg, state)

    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    combined = " ".join(texts).lower()
    assert "распознать" in combined or "формат" in combined


@pytest.mark.asyncio
async def test_reschedule_cancel_text(
    group_chat, telegram_user, now_dt,
):
    from bot.handlers.meetings import process_reschedule_datetime
    from aiogram.fsm.context import FSMContext

    msg = make_message(group_chat, telegram_user, "отмена", now_dt)

    state = AsyncMock(spec=FSMContext)
    state.clear = AsyncMock()

    await process_reschedule_datetime(msg, state)

    state.clear.assert_called_once()
    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    combined = " ".join(texts).lower()
    assert "отмен" in combined

# ─────────────────────────────────────────────────────────────────────────────
# BATCH MEETING EXTRACTION TESTS
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db_message_factory():
    """Фабрика для создания мок-сообщений из БД."""

    def _create(
        msg_id: int,
        text: str,
        username: str = "testuser",
        full_name: str = "Test User",
        telegram_id: int = 12345,
        chat_id: int = -100123,
        thread_id: int = 0,
        timestamp: datetime = None,
    ):
        if timestamp is None:
            timestamp = timezone.now()

        msg = MagicMock()
        msg.id = msg_id
        msg.text = text
        msg.timestamp = timestamp
        msg.is_processed = False
        msg.save = MagicMock()

        msg.author = MagicMock()
        msg.author.username = username
        msg.author.full_name = full_name
        msg.author.telegram_id = telegram_id
        msg.author.is_bot = False

        msg.chat = MagicMock()
        msg.chat.chat_id = chat_id

        msg.topic = MagicMock()
        msg.topic.thread_id = thread_id

        return msg

    return _create


class TestBatchMeetingExtraction:
    """Тесты для extract_meetings_from_messages_batch в MeetingService."""

    @pytest.mark.asyncio
    async def test_batch_empty_messages(self):
        """Пустой список → пустой результат."""
        from core.services.meeting_service import MeetingService

        service = MeetingService(llm=MagicMock())
        result = await service.extract_meetings_from_messages_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_single_meeting(self, mock_db_message_factory):
        """Одно сообщение в батче с встречей → встреча создана."""
        from core.services.meeting_service import MeetingService

        msg = mock_db_message_factory(
            msg_id=1,
            text="завтра в 10 встреча с заказчиком",
            username="boss",
        )

        tomorrow = (timezone.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")

        mock_llm = AsyncMock()
        mock_llm.extract_meetings_from_messages = AsyncMock(
            return_value=[
                {
                    "title": "встреча с заказчиком",
                    "participants": [],
                    "start_at": f"{tomorrow}T10:00:00",
                    "description": "",
                }
            ]
        )

        with patch(
            "core.services.meeting_service.sync_to_async",
            side_effect=lambda f: AsyncMock(return_value=f()),
        ):
            with patch("core.services.meeting_service.Meeting") as MockMeeting:
                mock_meeting_instance = MagicMock()
                mock_meeting_instance.id = 1
                mock_meeting_instance.participants = MagicMock()
                mock_meeting_instance.participants.add = MagicMock()
                MockMeeting.objects.create = MagicMock(
                    return_value=mock_meeting_instance
                )

                service = MeetingService(llm=mock_llm)
                result = await service.extract_meetings_from_messages_batch([msg])

        assert len(result) == 1
        mock_llm.extract_meetings_from_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_meeting_from_multiple_messages(
        self, mock_db_message_factory
    ):
        """
        Встреча описана в нескольких сообщениях →
        LLM получает их пачкой и извлекает одну встречу.
        """
        from core.services.meeting_service import MeetingService

        now = timezone.now()
        msg1 = mock_db_message_factory(
            msg_id=1,
            text="давайте завтра созвон",
            username="boss",
            timestamp=now - timedelta(minutes=2),
        )
        msg2 = mock_db_message_factory(
            msg_id=2,
            text="ок, в 11:30 норм?",
            username="worker1",
            timestamp=now - timedelta(minutes=1),
        )
        msg3 = mock_db_message_factory(
            msg_id=3,
            text="да, давайте",
            username="boss",
            timestamp=now,
        )

        tomorrow = (now.date() + timedelta(days=1)).strftime("%Y-%m-%d")

        mock_llm = AsyncMock()
        mock_llm.extract_meetings_from_messages = AsyncMock(
            return_value=[
                {
                    "title": "созвон",
                    "participants": ["@boss", "@worker1"],
                    "start_at": f"{tomorrow}T11:30:00",
                    "description": "",
                }
            ]
        )

        with patch(
            "core.services.meeting_service.sync_to_async",
            side_effect=lambda f: AsyncMock(return_value=f()),
        ):
            with patch("core.services.meeting_service.Meeting") as MockMeeting:
                mock_meeting_instance = MagicMock()
                mock_meeting_instance.id = 1
                mock_meeting_instance.participants = MagicMock()
                mock_meeting_instance.participants.add = MagicMock()
                MockMeeting.objects.create = MagicMock(
                    return_value=mock_meeting_instance
                )

                with patch(
                    "core.services.meeting_service._find_user_by_username",
                    return_value=MagicMock(id=1),
                ):
                    service = MeetingService(llm=mock_llm)
                    result = await service.extract_meetings_from_messages_batch(
                        [msg1, msg2, msg3]
                    )

        # Один вызов LLM
        assert mock_llm.extract_meetings_from_messages.call_count == 1

        # Проверяем что в батч-текст попали все сообщения
        call_args = mock_llm.extract_meetings_from_messages.call_args
        batch_text = call_args[0][0]
        assert "давайте завтра созвон" in batch_text
        assert "11:30" in batch_text

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_batch_no_meetings_found(self, mock_db_message_factory):
        """LLM не нашла встреч → пустой результат."""
        from core.services.meeting_service import MeetingService

        msg = mock_db_message_factory(
            msg_id=1,
            text="купи молоко",
            username="user1",
        )

        mock_llm = AsyncMock()
        mock_llm.extract_meetings_from_messages = AsyncMock(return_value=[])

        service = MeetingService(llm=mock_llm)
        result = await service.extract_meetings_from_messages_batch([msg])

        assert result == []

    @pytest.mark.asyncio
    async def test_batch_llm_error_returns_empty(self, mock_db_message_factory):
        """LLM ошибка → пустой список, без краша."""
        from core.services.meeting_service import MeetingService

        msg = mock_db_message_factory(
            msg_id=1,
            text="встреча завтра в 9",
            username="boss",
        )

        mock_llm = AsyncMock()
        mock_llm.extract_meetings_from_messages = AsyncMock(
            side_effect=Exception("API timeout")
        )

        service = MeetingService(llm=mock_llm)
        result = await service.extract_meetings_from_messages_batch([msg])

        assert result == []

    @pytest.mark.asyncio
    async def test_batch_multiple_meetings(self, mock_db_message_factory):
        """Две встречи в батче → обе создаются."""
        from core.services.meeting_service import MeetingService

        now = timezone.now()
        msg1 = mock_db_message_factory(
            msg_id=1,
            text="завтра в 9 встреча с заказчиком",
            username="boss",
            timestamp=now - timedelta(minutes=1),
        )
        msg2 = mock_db_message_factory(
            msg_id=2,
            text="и ещё в 14:00 созвон с дизайнером",
            username="boss",
            timestamp=now,
        )

        tomorrow = (now.date() + timedelta(days=1)).strftime("%Y-%m-%d")

        mock_llm = AsyncMock()
        mock_llm.extract_meetings_from_messages = AsyncMock(
            return_value=[
                {
                    "title": "встреча с заказчиком",
                    "participants": [],
                    "start_at": f"{tomorrow}T09:00:00",
                    "description": "",
                },
                {
                    "title": "созвон с дизайнером",
                    "participants": [],
                    "start_at": f"{tomorrow}T14:00:00",
                    "description": "",
                },
            ]
        )

        meeting_counter = {"count": 0}

        def make_meeting(**kwargs):
            meeting_counter["count"] += 1
            m = MagicMock()
            m.id = meeting_counter["count"]
            m.participants = MagicMock()
            m.participants.add = MagicMock()
            return m

        with patch(
            "core.services.meeting_service.sync_to_async",
            side_effect=lambda f: AsyncMock(return_value=f()),
        ):
            with patch("core.services.meeting_service.Meeting") as MockMeeting:
                MockMeeting.objects.create = MagicMock(side_effect=make_meeting)

                service = MeetingService(llm=mock_llm)
                result = await service.extract_meetings_from_messages_batch(
                    [msg1, msg2]
                )

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_batch_source_message_is_last(self, mock_db_message_factory):
        """Встречи привязываются к последнему сообщению батча."""
        from core.services.meeting_service import MeetingService

        now = timezone.now()
        msg1 = mock_db_message_factory(
            msg_id=1,
            text="завтра созвон",
            username="boss",
            timestamp=now - timedelta(minutes=1),
        )
        msg2 = mock_db_message_factory(
            msg_id=2,
            text="в 10:00",
            username="boss",
            timestamp=now,
        )

        tomorrow = (now.date() + timedelta(days=1)).strftime("%Y-%m-%d")

        mock_llm = AsyncMock()
        mock_llm.extract_meetings_from_messages = AsyncMock(
            return_value=[
                {
                    "title": "созвон",
                    "participants": [],
                    "start_at": f"{tomorrow}T10:00:00",
                    "description": "",
                }
            ]
        )

        created_kwargs = {}

        def capture_create(**kwargs):
            created_kwargs.update(kwargs)
            m = MagicMock()
            m.id = 1
            m.participants = MagicMock()
            m.participants.add = MagicMock()
            return m

        def fake_sync_to_async(func):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        with patch(
            "core.services.meeting_service.sync_to_async",
            side_effect=fake_sync_to_async,
        ):
            with patch("core.services.meeting_service.Meeting") as MockMeeting:
                MockMeeting.objects.create = MagicMock(side_effect=capture_create)

                service = MeetingService(llm=mock_llm)
                await service.extract_meetings_from_messages_batch(
                    [msg1, msg2]
                )

        assert created_kwargs.get("source_message") == msg2

    @pytest.mark.asyncio
    async def test_batch_participant_not_found_skipped(
        self, mock_db_message_factory
    ):
        """Участник не найден в БД → пропускается, встреча всё равно создана."""
        from core.services.meeting_service import MeetingService

        tomorrow = (timezone.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")

        msg = mock_db_message_factory(
            msg_id=1,
            text="завтра в 10 встреча @unknown_person",
            username="boss",
        )

        mock_llm = AsyncMock()
        mock_llm.extract_meetings_from_messages = AsyncMock(
            return_value=[
                {
                    "title": "встреча",
                    "participants": ["@unknown_person"],
                    "start_at": f"{tomorrow}T10:00:00",
                    "description": "",
                }
            ]
        )

        mock_meeting_instance = MagicMock()
        mock_meeting_instance.id = 1
        mock_meeting_instance.participants = MagicMock()
        mock_meeting_instance.participants.add = MagicMock()

        with patch(
            "core.services.meeting_service.sync_to_async",
            side_effect=lambda f: AsyncMock(return_value=f()),
        ):
            with patch("core.services.meeting_service.Meeting") as MockMeeting:
                MockMeeting.objects.create = MagicMock(
                    return_value=mock_meeting_instance
                )

                with patch(
                    "core.services.meeting_service._find_user_by_username",
                    return_value=None,
                ):
                    service = MeetingService(llm=mock_llm)
                    result = await service.extract_meetings_from_messages_batch([msg])

        assert len(result) == 1
        # participants.add не вызван, т.к. пользователь не найден
        mock_meeting_instance.participants.add.assert_not_called()