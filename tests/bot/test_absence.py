"""
Тесты для недоступности пользователей (/away, /back, force-назначение).
"""
import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch
from django.utils import timezone


# ═══════════════════════════════════════════════════════════════
# Фикстуры
# ═══════════════════════════════════════════════════════════════
@pytest.fixture
def frozen_now():
    """Фиксирует текущее время для детерминированных тестов парсера дат."""
    tz = timezone.get_current_timezone()
    fixed = datetime(2026, 8, 25, 12, 0, 0, tzinfo=tz)  # вторник
    with patch("core.services.absence_service.timezone.now", return_value=fixed):
        with patch("core.services.absence_service.timezone.localtime",
                   side_effect=lambda dt: dt):
            yield fixed


@pytest.fixture
def mock_absence():
    absence = MagicMock()
    absence.id = 1
    absence.end = datetime(2026, 9, 1, 23, 59, 0, tzinfo=dt_timezone.utc)
    absence.reason = "отпуск"
    absence.user = MagicMock()
    return absence


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 1
    user.telegram_id = 111
    user.username = "testuser"
    user.full_name = "Test User"
    return user


# ═══════════════════════════════════════════════════════════════
# Парсер дат для /away
# ═══════════════════════════════════════════════════════════════
class TestParseAbsenceDuration:
    def test_parse_for_n_days(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        result = parse_absence_duration("на 3 дня")
        assert result is not None
        expected_date = (frozen_now + timedelta(days=3)).date()
        assert result.date() == expected_date
        assert result.hour == 23
        assert result.minute == 59

    def test_parse_for_week(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        result = parse_absence_duration("на неделю")
        assert result is not None
        expected_date = (frozen_now + timedelta(weeks=1)).date()
        assert result.date() == expected_date

    def test_parse_for_n_weeks(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        result = parse_absence_duration("на 2 недели")
        assert result is not None
        expected_date = (frozen_now + timedelta(weeks=2)).date()
        assert result.date() == expected_date

    def test_parse_for_month(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        result = parse_absence_duration("на месяц")
        assert result is not None
        expected_date = (frozen_now + timedelta(days=30)).date()
        assert result.date() == expected_date

    def test_parse_for_n_hours(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        result = parse_absence_duration("на 5 часов")
        assert result is not None
        expected = frozen_now + timedelta(hours=5)
        assert result.hour == expected.hour

    def test_parse_until_tomorrow(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        result = parse_absence_duration("до завтра")
        assert result is not None
        expected_date = (frozen_now + timedelta(days=1)).date()
        assert result.date() == expected_date

    def test_parse_until_date_future_this_year(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        # 25 декабря 2026 — в будущем от 25 августа 2026
        result = parse_absence_duration("до 25.12")
        assert result is not None
        assert result.day == 25
        assert result.month == 12
        assert result.year == 2026

    def test_parse_until_date_past_shifts_to_next_year(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        # 25 мая 2026 уже прошло (сейчас 25 августа) → следующий год
        result = parse_absence_duration("до 25.05")
        assert result is not None
        assert result.day == 25
        assert result.month == 5
        assert result.year == 2027

    def test_parse_until_date_with_explicit_year(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        result = parse_absence_duration("до 25.05.2028")
        assert result is not None
        assert result.year == 2028
        assert result.month == 5
        assert result.day == 25

    def test_parse_iso_date(self, frozen_now):
        from core.services.absence_service import parse_absence_duration
        result = parse_absence_duration("до 2027-03-15")
        assert result is not None
        assert result.year == 2027
        assert result.month == 3
        assert result.day == 15

    def test_parse_empty_returns_none(self):
        from core.services.absence_service import parse_absence_duration
        assert parse_absence_duration("") is None

    def test_parse_invalid_returns_none(self):
        from core.services.absence_service import parse_absence_duration
        assert parse_absence_duration("абракадабра") is None

    def test_parse_invalid_date_returns_none(self):
        from core.services.absence_service import parse_absence_duration
        # 99-е число не существует
        assert parse_absence_duration("до 99.99") is None


# ═══════════════════════════════════════════════════════════════
# Форматирование недоступности
# ═══════════════════════════════════════════════════════════════
class TestFormatAbsence:
    def test_format_absence_with_reason(self):
        from core.services.absence_service import format_absence
        absence = MagicMock()
        absence.end = datetime(2026, 5, 25, 23, 59, 0)  # naive → без конвертации
        absence.reason = "отпуск"
        result = format_absence(absence)
        assert "недоступен до 25.05.2026" in result
        assert "отпуск" in result

    def test_format_absence_without_reason(self):
        from core.services.absence_service import format_absence
        absence = MagicMock()
        absence.end = datetime(2026, 5, 25, 23, 59, 0)
        absence.reason = ""
        result = format_absence(absence)
        assert "недоступен до 25.05.2026" in result
        assert "(" not in result


# ═══════════════════════════════════════════════════════════════
# AbsenceService
# ═══════════════════════════════════════════════════════════════
class TestAbsenceService:
    @pytest.mark.asyncio
    async def test_is_absent_returns_absence(self, mock_user, mock_absence):
        from core.services.absence_service import AbsenceService
        svc = AbsenceService()
        with patch("core.services.absence_service.is_absent_now_sync",
                   return_value=mock_absence):
            result = await svc.is_absent(mock_user)
        assert result == mock_absence

    @pytest.mark.asyncio
    async def test_is_absent_returns_none(self, mock_user):
        from core.services.absence_service import AbsenceService
        svc = AbsenceService()
        with patch("core.services.absence_service.is_absent_now_sync",
                   return_value=None):
            result = await svc.is_absent(mock_user)
        assert result is None

    @pytest.mark.asyncio
    async def test_filter_absent_assignees(self, mock_user):
        from core.services.absence_service import AbsenceService
        svc = AbsenceService()
        absent_list = [mock_user]
        with patch("core.services.absence_service.get_absent_users_sync",
                   return_value=absent_list):
            result = await svc.filter_absent_assignees([mock_user])
        assert result == absent_list

    @pytest.mark.asyncio
    async def test_create_absence_success(self, mock_user):
        from core.services.absence_service import AbsenceService
        svc = AbsenceService()
        start = datetime(2026, 8, 25, 12, 0, 0, tzinfo=dt_timezone.utc)
        end = datetime(2026, 9, 1, 23, 59, 0, tzinfo=dt_timezone.utc)
        created = MagicMock()
        with patch("core.models.UserAbsence.objects.create",
                   return_value=created) as mock_create:
            result = await svc.create_absence(mock_user, start, end, "отпуск")
        assert result == created
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_absence_invalid_dates_raises(self, mock_user):
        from core.services.absence_service import AbsenceService
        svc = AbsenceService()
        start = datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
        end = datetime(2026, 8, 25, 12, 0, 0, tzinfo=dt_timezone.utc)  # раньше start
        with pytest.raises(ValueError):
            await svc.create_absence(mock_user, start, end)

    @pytest.mark.asyncio
    async def test_cancel_all_active(self, mock_user):
        from core.services.absence_service import AbsenceService
        svc = AbsenceService()
        with patch("core.models.UserAbsence.objects.filter") as mock_filter:
            mock_filter.return_value.delete.return_value = (2, {})
            result = await svc.cancel_all_active(mock_user)
        assert result == 2

    @pytest.mark.asyncio
    async def test_cancel_absence(self, mock_user):
        from core.services.absence_service import AbsenceService
        svc = AbsenceService()
        with patch("core.models.UserAbsence.objects.filter") as mock_filter:
            chain = mock_filter.return_value
            chain.filter.return_value = chain  # любой последующий filter возвращает тот же объект
            chain.delete.return_value = (1, {})
            result = await svc.cancel_absence(1, user=mock_user)
        assert result is True


# ═══════════════════════════════════════════════════════════════
# Клавиатуры
# ═══════════════════════════════════════════════════════════════
class TestKeyboards:
    def test_force_assign_task_keyboard(self):
        from bot.keyboards.inline import force_assign_keyboard
        markup = force_assign_keyboard(pending_id=42, kind="task")
        assert markup is not None
        # Должна быть кнопка "Всё равно назначить"
        buttons = [btn.text for row in markup.inline_keyboard for btn in row]
        assert any("всё равно назначить" in b.lower() for b in buttons)

    def test_force_assign_meeting_keyboard(self):
        from bot.keyboards.inline import force_assign_keyboard
        markup = force_assign_keyboard(pending_id=42, kind="meeting")
        assert markup is not None
        buttons = [btn.text for row in markup.inline_keyboard for btn in row]
        assert any("всё равно участвует" in b.lower() for b in buttons)

    def test_absence_cancel_keyboard(self):
        from bot.keyboards.inline import absence_cancel_keyboard
        markup = absence_cancel_keyboard(absence_id=1)
        assert markup is not None


# ═══════════════════════════════════════════════════════════════
# Команда /away
# ═══════════════════════════════════════════════════════════════
class TestAwayCommand:
    @pytest.mark.asyncio
    async def test_away_no_user(self, private_chat, telegram_user, now_dt):
        from bot.handlers.away import cmd_away
        from tests.conftest import make_message
        msg = make_message(private_chat, telegram_user, "/away", now_dt)
        with patch("bot.handlers.away.get_chat_context") as mock_ctx:
            mock_ctx.return_value = (MagicMock(), None, None)
            await cmd_away(msg)
        assert "Не удалось определить пользователя" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_away_shows_help_on_empty_args(self, private_chat, telegram_user, now_dt, mock_user):
        from bot.handlers.away import cmd_away
        from tests.conftest import make_message
        msg = make_message(private_chat, telegram_user, "/away", now_dt)
        with patch("bot.handlers.away.get_chat_context") as mock_ctx:
            mock_ctx.return_value = (MagicMock(), None, mock_user)
            await cmd_away(msg)
        assert "Недоступность" in msg.answer.call_args[0][0]
        assert "Примеры" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_away_shows_list(self, private_chat, telegram_user, now_dt, mock_user, mock_absence):
        from bot.handlers.away import cmd_away
        from tests.conftest import make_message
        msg = make_message(private_chat, telegram_user, "/away список", now_dt)
        with patch("bot.handlers.away.get_chat_context") as mock_ctx, \
             patch("bot.handlers.away.absence_service") as mock_svc:
            mock_ctx.return_value = (MagicMock(), None, mock_user)
            mock_svc.get_active_absences = AsyncMock(return_value=[mock_absence])
            await cmd_away(msg)
        assert "недоступности" in msg.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_away_shows_empty_list(self, private_chat, telegram_user, now_dt, mock_user):
        from bot.handlers.away import cmd_away
        from tests.conftest import make_message
        msg = make_message(private_chat, telegram_user, "/away список", now_dt)
        with patch("bot.handlers.away.get_chat_context") as mock_ctx, \
             patch("bot.handlers.away.absence_service") as mock_svc:
            mock_ctx.return_value = (MagicMock(), None, mock_user)
            mock_svc.get_active_absences = AsyncMock(return_value=[])
            await cmd_away(msg)
        assert "нет активных" in msg.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_away_creates_absence(self, private_chat, telegram_user, now_dt, mock_user):
        from bot.handlers.away import cmd_away
        from tests.conftest import make_message
        msg = make_message(private_chat, telegram_user, "/away на неделю", now_dt)
        created_absence = MagicMock()
        created_absence.id = 5
        created_absence.end = datetime(2026, 9, 1, 23, 59, 0, tzinfo=dt_timezone.utc)
        with patch("bot.handlers.away.get_chat_context") as mock_ctx, \
             patch("bot.handlers.away.absence_service") as mock_svc:
            mock_ctx.return_value = (MagicMock(), None, mock_user)
            mock_svc.create_absence = AsyncMock(return_value=created_absence)
            await cmd_away(msg)
        mock_svc.create_absence.assert_called_once()
        assert "недоступны" in msg.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_back_cancels_all(self, private_chat, telegram_user, now_dt, mock_user):
        from bot.handlers.away import cmd_back
        from tests.conftest import make_message
        msg = make_message(private_chat, telegram_user, "/back", now_dt)
        with patch("bot.handlers.away.get_chat_context") as mock_ctx, \
             patch("bot.handlers.away.absence_service") as mock_svc:
            mock_ctx.return_value = (MagicMock(), None, mock_user)
            mock_svc.cancel_all_active = AsyncMock(return_value=2)
            await cmd_back(msg)
        assert "Снова доступны" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_back_no_active(self, private_chat, telegram_user, now_dt, mock_user):
        from bot.handlers.away import cmd_back
        from tests.conftest import make_message
        msg = make_message(private_chat, telegram_user, "/back", now_dt)
        with patch("bot.handlers.away.get_chat_context") as mock_ctx, \
             patch("bot.handlers.away.absence_service") as mock_svc:
            mock_ctx.return_value = (MagicMock(), None, mock_user)
            mock_svc.cancel_all_active = AsyncMock(return_value=0)
            await cmd_back(msg)
        assert "и так доступны" in msg.answer.call_args[0][0]


# ═══════════════════════════════════════════════════════════════
# Callback "всё равно назначить" / "всё равно участвует"
# ═══════════════════════════════════════════════════════════════
class TestForceAssignCallbacks:
    @pytest.mark.asyncio
    async def test_force_assign_task_success(self):
        from bot.handlers.away import callback_force_assign_task

        task_mock = MagicMock()
        task_mock.id = 10
        task_mock.title = "Отчёт"
        user_mock = MagicMock()
        user_mock.username = "jdex"
        user_mock.full_name = "JDex"

        pending_mock = MagicMock()
        pending_mock.task = task_mock
        pending_mock.user = user_mock
        pending_mock.delete = MagicMock()

        callback = MagicMock()
        callback.data = "force_assign_task:1"
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()

        with patch("bot.handlers.away.PendingAssignment.objects") as mock_pa, \
             patch("bot.handlers.away.TaskAssignee.objects") as mock_ta, \
             patch("celery_app.tasks.send_reminders.send_task_assigned_notification") as mock_notify:
            mock_pa.filter.return_value.select_related.return_value.first.return_value = pending_mock
            mock_ta.filter.return_value.exists.return_value = False

            await callback_force_assign_task(callback)

            mock_ta.create.assert_called_once()
            mock_notify.delay.assert_called_once_with(10)

    @pytest.mark.asyncio
    async def test_force_assign_meeting_success(self):
        from bot.handlers.away import callback_force_assign_meeting

        meeting_mock = MagicMock()
        meeting_mock.id = 20
        meeting_mock.title = "Планёрка"
        meeting_mock.participants = MagicMock()
        meeting_mock.participants.add = MagicMock()
        user_mock = MagicMock()
        user_mock.username = "jdex"

        pending_mock = MagicMock()
        pending_mock.meeting = meeting_mock
        pending_mock.user = user_mock
        pending_mock.delete = MagicMock()

        callback = MagicMock()
        callback.data = "force_assign_meeting:2"
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()

        with patch("bot.handlers.away.PendingAssignment.objects") as mock_pa, \
             patch("celery_app.tasks.send_reminders.send_meeting_assigned_notification") as mock_notify:
            mock_pa.filter.return_value.select_related.return_value.first.return_value = pending_mock

            await callback_force_assign_meeting(callback)

            meeting_mock.participants.add.assert_called_once_with(user_mock)
            mock_notify.delay.assert_called_once_with(20)

    @pytest.mark.asyncio
    async def test_force_assign_skip(self):
        from bot.handlers.away import callback_force_assign_skip

        callback = MagicMock()
        callback.data = "force_assign_skip:3"
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()

        with patch("bot.handlers.away.PendingAssignment.objects") as mock_pa:
            mock_pa.filter.return_value.delete.return_value = (1, {})
            await callback_force_assign_skip(callback)

        mock_pa.filter.assert_called_once()
        callback.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_absence_cancel_callback(self):
        from bot.handlers.away import callback_absence_cancel

        user_mock = MagicMock()
        user_mock.id = 1

        callback = MagicMock()
        callback.data = "absence_cancel:7"
        callback.from_user.id = 111
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()

        with patch("bot.handlers.away.TelegramUser.objects") as mock_tu, \
             patch("bot.handlers.away.absence_service") as mock_svc:
            mock_tu.filter.return_value.first.return_value = user_mock
            mock_svc.cancel_absence = AsyncMock(return_value=True)

            await callback_absence_cancel(callback)

            mock_svc.cancel_absence.assert_called_once()
            callback.answer.assert_called_once()