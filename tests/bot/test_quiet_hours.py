"""
Тесты для тихих часов и политики доставки уведомлений.
"""
import pytest
from datetime import datetime, timedelta, timezone as dt_timezone, time as dtime
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# is_quiet_hours_sync
# ═══════════════════════════════════════════════════════════════
class TestIsQuietHours:
    def test_within_normal_range(self):
        """Диапазон 13:00-14:00, время 13:30 → тихо."""
        from core.services.notification_policy import is_quiet_hours_sync
        dt = datetime(2026, 8, 25, 13, 30, tzinfo=dt_timezone.utc)
        assert is_quiet_hours_sync(dt, dtime(13, 0), dtime(14, 0)) is True

    def test_outside_normal_range(self):
        """Диапазон 13:00-14:00, время 15:00 → не тихо."""
        from core.services.notification_policy import is_quiet_hours_sync
        dt = datetime(2026, 8, 25, 15, 0, tzinfo=dt_timezone.utc)
        assert is_quiet_hours_sync(dt, dtime(13, 0), dtime(14, 0)) is False

    def test_overnight_inside_evening(self):
        """Диапазон 20:00-8:00, время 22:00 → тихо."""
        from core.services.notification_policy import is_quiet_hours_sync
        dt = datetime(2026, 8, 25, 22, 0, tzinfo=dt_timezone.utc)
        assert is_quiet_hours_sync(dt, dtime(20, 0), dtime(8, 0)) is True

    def test_overnight_early_morning(self):
        """Диапазон 20:00-8:00, время 5:00 → тихо."""
        from core.services.notification_policy import is_quiet_hours_sync
        dt = datetime(2026, 8, 25, 5, 0, tzinfo=dt_timezone.utc)
        assert is_quiet_hours_sync(dt, dtime(20, 0), dtime(8, 0)) is True

    def test_overnight_daytime(self):
        """Диапазон 20:00-8:00, время 12:00 → не тихо."""
        from core.services.notification_policy import is_quiet_hours_sync
        dt = datetime(2026, 8, 25, 12, 0, tzinfo=dt_timezone.utc)
        assert is_quiet_hours_sync(dt, dtime(20, 0), dtime(8, 0)) is False

    def test_boundary_start_inclusive(self):
        """Диапазон 20:00-8:00, ровно 20:00 → тихо (граница включается)."""
        from core.services.notification_policy import is_quiet_hours_sync
        dt = datetime(2026, 8, 25, 20, 0, tzinfo=dt_timezone.utc)
        assert is_quiet_hours_sync(dt, dtime(20, 0), dtime(8, 0)) is True

    def test_boundary_end_exclusive(self):
        """Диапазон 20:00-8:00, ровно 8:00 → не тихо."""
        from core.services.notification_policy import is_quiet_hours_sync
        dt = datetime(2026, 8, 25, 8, 0, tzinfo=dt_timezone.utc)
        assert is_quiet_hours_sync(dt, dtime(20, 0), dtime(8, 0)) is False


# ═══════════════════════════════════════════════════════════════
# next_delivery_time_sync
# ═══════════════════════════════════════════════════════════════
class TestNextDeliveryTime:
    def test_night_returns_next_morning(self):
        """Вторник 22:00 → среда 8:00."""
        from core.services.notification_policy import next_delivery_time_sync
        now = datetime(2026, 8, 25, 22, 0, tzinfo=dt_timezone.utc)  # вторник
        result = next_delivery_time_sync(now)
        assert result.day == 26  # среда
        assert result.hour == 8
        assert result.minute == 0

    def test_early_morning_returns_today_8am(self):
        """Вторник 5:00 → сегодня (вторник) 8:00."""
        from core.services.notification_policy import next_delivery_time_sync
        now = datetime(2026, 8, 25, 5, 0, tzinfo=dt_timezone.utc)  # вторник
        result = next_delivery_time_sync(now)
        assert result.day == 25
        assert result.hour == 8

    def test_after_8am_returns_next_day(self):
        """Вторник 12:00 → среда 8:00."""
        from core.services.notification_policy import next_delivery_time_sync
        now = datetime(2026, 8, 25, 12, 0, tzinfo=dt_timezone.utc)  # вторник
        result = next_delivery_time_sync(now)
        assert result.day == 26  # среда
        assert result.hour == 8

    def test_friday_night_skips_weekend(self):
        """Пятница 22:00 → понедельник 8:00 (пропуск сб/вс)."""
        from core.services.notification_policy import next_delivery_time_sync
        now = datetime(2026, 8, 28, 22, 0, tzinfo=dt_timezone.utc)  # пятница
        result = next_delivery_time_sync(now)
        assert result.day == 31  # понедельник
        assert result.weekday() == 0
        assert result.hour == 8

    def test_saturday_returns_monday(self):
        """Суббота 10:00 → понедельник 8:00."""
        from core.services.notification_policy import next_delivery_time_sync
        now = datetime(2026, 8, 29, 10, 0, tzinfo=dt_timezone.utc)  # суббота
        result = next_delivery_time_sync(now)
        assert result.day == 31  # понедельник
        assert result.weekday() == 0


# ═══════════════════════════════════════════════════════════════
# evaluate_sync (решение о доставке)
# ═══════════════════════════════════════════════════════════════
class TestEvaluate:
    def _make_user_with_settings(self, quiet_enabled=True, quiet_start=dtime(20, 0),
                                  quiet_end=dtime(8, 0), skip_weekends=False):
        user = MagicMock()
        user.notif_settings = MagicMock()
        user.notif_settings.quiet_hours_enabled = quiet_enabled
        user.notif_settings.quiet_start = quiet_start
        user.notif_settings.quiet_end = quiet_end
        user.notif_settings.skip_weekends = skip_weekends
        return user

    def test_send_when_available(self):
        from core.services.notification_policy import evaluate_sync, Decision
        user = self._make_user_with_settings(quiet_start=dtime(20, 0), quiet_end=dtime(8, 0))
        now = datetime(2026, 8, 25, 12, 0, tzinfo=dt_timezone.utc)  # вторник, день
        with patch("core.services.absence_service.is_absent_now_sync", return_value=None):
            result = evaluate_sync(user, now)
        assert result == Decision.SEND

    def test_defer_at_night(self):
        from core.services.notification_policy import evaluate_sync, Decision
        user = self._make_user_with_settings(quiet_start=dtime(20, 0), quiet_end=dtime(8, 0))
        now = datetime(2026, 8, 25, 22, 0, tzinfo=dt_timezone.utc)  # вторник, ночь
        with patch("core.services.absence_service.is_absent_now_sync", return_value=None):
            result = evaluate_sync(user, now)
        assert result == Decision.DEFER

    def test_defer_on_weekend(self):
        from core.services.notification_policy import evaluate_sync, Decision
        user = self._make_user_with_settings(skip_weekends=True)
        now = datetime(2026, 8, 29, 12, 0, tzinfo=dt_timezone.utc)  # суббота, день
        with patch("core.services.absence_service.is_absent_now_sync", return_value=None):
            result = evaluate_sync(user, now)
        assert result == Decision.DEFER

    def test_send_on_weekend_if_disabled(self):
        from core.services.notification_policy import evaluate_sync, Decision
        user = self._make_user_with_settings(skip_weekends=False)
        now = datetime(2026, 8, 29, 12, 0, tzinfo=dt_timezone.utc)  # суббота, день
        with patch("core.services.absence_service.is_absent_now_sync", return_value=None):
            result = evaluate_sync(user, now)
        assert result == Decision.SEND

    def test_skip_when_absent(self):
        from core.services.notification_policy import evaluate_sync, Decision
        user = self._make_user_with_settings()
        now = datetime(2026, 8, 25, 12, 0, tzinfo=dt_timezone.utc)
        with patch("core.services.absence_service.is_absent_now_sync",
                   return_value=MagicMock()):
            result = evaluate_sync(user, now)
        assert result == Decision.SKIP

    def test_send_when_quiet_hours_disabled(self):
        from core.services.notification_policy import evaluate_sync, Decision
        user = self._make_user_with_settings(quiet_enabled=False)
        now = datetime(2026, 8, 25, 22, 0, tzinfo=dt_timezone.utc)  # ночь
        with patch("core.services.absence_service.is_absent_now_sync", return_value=None):
            result = evaluate_sync(user, now)
        assert result == Decision.SEND

    def test_send_with_default_settings_when_night(self):
        """Если нет настроек — применяются границы по умолчанию (20:00-8:00)."""
        from core.services.notification_policy import evaluate_sync, Decision
        user = MagicMock()
        # user.notif_settings выбросит DoesNotExist? Нет, MagicMock не бросает.
        # Поэтому симулируем отсутствие настроек через side_effect
        from core.models import UserNotificationSettings
        type(user).notif_settings = property(
            lambda self: (_ for _ in ()).throw(UserNotificationSettings.DoesNotExist())
        )
        now = datetime(2026, 8, 25, 22, 0, tzinfo=dt_timezone.utc)  # ночь
        with patch("core.services.absence_service.is_absent_now_sync", return_value=None):
            result = evaluate_sync(user, now)
        assert result == Decision.DEFER


# ═══════════════════════════════════════════════════════════════
# NotificationSender с политикой
# ═══════════════════════════════════════════════════════════════
class TestNotificationSenderPolicy:
    def _make_sender(self):
        from bot.services.notification_sender import NotificationSender
        bot = AsyncMock()
        bot.send_message = AsyncMock()
        return NotificationSender(bot)

    @pytest.mark.asyncio
    async def test_send_when_send_decision(self):
        from core.services.notification_policy import Decision
        sender = self._make_sender()
        user = MagicMock()
        user.telegram_id = 111

        with patch("core.services.notification_policy.evaluate",
                   new_callable=AsyncMock, return_value=Decision.SEND):
            result = await sender.send_notification(user, "текст")

        assert result is True
        sender.bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_defer_creates_celery_task(self):
        from core.services.notification_policy import Decision
        sender = self._make_sender()
        user = MagicMock()
        user.telegram_id = 111
        eta = datetime(2026, 8, 26, 8, 0, tzinfo=dt_timezone.utc)

        with patch("core.services.notification_policy.evaluate",
                   new_callable=AsyncMock, return_value=Decision.DEFER), \
             patch("core.services.notification_policy.next_delivery_time",
                   new_callable=AsyncMock, return_value=eta), \
             patch("celery_app.tasks.deferred_notifications.send_deferred_notification") as mock_task:
            result = await sender.send_notification(user, "текст")

        assert result is False
        mock_task.apply_async.assert_called_once()
        # Сообщение НЕ отправлено напрямую
        sender.bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_absent(self):
        from core.services.notification_policy import Decision
        sender = self._make_sender()
        user = MagicMock()
        user.telegram_id = 111

        with patch("core.services.notification_policy.evaluate",
                   new_callable=AsyncMock, return_value=Decision.SKIP):
            result = await sender.send_notification(user, "текст")

        assert result is False
        sender.bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_user_returns_false(self):
        sender = self._make_sender()
        result = await sender.send_notification(None, "текст")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_reminder_delegates(self):
        from core.services.notification_policy import Decision
        sender = self._make_sender()
        user = MagicMock()
        user.telegram_id = 111

        with patch("core.services.notification_policy.evaluate",
                   new_callable=AsyncMock, return_value=Decision.SEND):
            result = await sender.send_reminder(user, "напоминание")

        assert result is True


# ═══════════════════════════════════════════════════════════════
# Отложенные уведомления (deferred_notifications)
# ═══════════════════════════════════════════════════════════════
class TestDeferredNotification:
    @pytest.mark.asyncio
    async def test_deferred_sends_when_available(self):
        from celery_app.tasks.deferred_notifications import _send_deferred_async
        from core.services.notification_policy import Decision

        user = MagicMock()
        user.telegram_id = 111
        bot = AsyncMock()
        bot.send_message = AsyncMock()
        bot.session = MagicMock()
        bot.session.close = AsyncMock()

        with patch("aiogram.Bot", return_value=bot), \
             patch("core.services.absence_service.is_absent_now_sync", return_value=None), \
             patch("core.services.notification_policy.evaluate",
                   new_callable=AsyncMock, return_value=Decision.SEND), \
             patch("core.models.TelegramUser.objects.filter") as mock_filter:
            mock_filter.return_value.first.return_value = user
            result = await _send_deferred_async(111, "текст")

        assert result is True
        bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_deferred_drops_when_absent(self):
        from celery_app.tasks.deferred_notifications import _send_deferred_async
        from core.services.notification_policy import Decision

        user = MagicMock()
        user.telegram_id = 111
        bot = AsyncMock()
        bot.session = MagicMock()
        bot.session.close = AsyncMock()

        with patch("aiogram.Bot", return_value=bot), \
             patch("core.services.notification_policy.evaluate",
                   new_callable=AsyncMock, return_value=Decision.SKIP), \
             patch("core.models.TelegramUser.objects.filter") as mock_filter:
            mock_filter.return_value.first.return_value = user
            result = await _send_deferred_async(111, "текст")

        assert result is False
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_deferred_user_not_found(self):
        from celery_app.tasks.deferred_notifications import _send_deferred_async

        bot = AsyncMock()
        bot.session = MagicMock()
        bot.session.close = AsyncMock()

        with patch("aiogram.Bot", return_value=bot), \
             patch("core.models.TelegramUser.objects.filter") as mock_filter:
            mock_filter.return_value.first.return_value = None
            result = await _send_deferred_async(999, "текст")

        assert result is False
        bot.send_message.assert_not_called()