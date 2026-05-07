import pytest
from unittest.mock import MagicMock


def _make_user(telegram_id=999, username="testuser", full_name="Test User", is_bot=False):
    user = MagicMock()
    user.telegram_id = telegram_id
    user.username = username
    user.full_name = full_name
    user.is_bot = is_bot
    return user


class TestIsBotUser:

    def test_is_bot_flag(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=True, username="regular")) is True

    def test_bot_username_suffix(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username="Neirix1_bot")) is True

    def test_bot_username_capital(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username="MyBot")) is True

    def test_regular_user(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username="JDexMoment")) is False

    def test_no_username(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username=None)) is False

    def test_bot_in_middle(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username="botmaster")) is False

    def test_underscore_bot_suffix(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username="helper_Bot")) is True

    def test_empty_username(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username="")) is False

    def test_just_bot(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username="Bot")) is True

    def test_just_bot_lowercase(self):
        from celery_app.tasks.send_reminders import _is_bot_user
        assert _is_bot_user(_make_user(is_bot=False, username="bot")) is True