import pytest
from unittest.mock import MagicMock, patch

from asgiref.sync import async_to_sync
from django.utils import timezone

from core.models import TelegramChat, TelegramUser, Topic, Message as DBMessage


@pytest.mark.django_db(transaction=True)
class TestMessageHandlerForumFlag:

    def test_handle_text_message_supergroup_with_none_is_forum_saved_as_false(self, monkeypatch):
        """
        Если Telegram прислал chat.is_forum=None для supergroup,
        хендлер должен сохранить is_forum=False, положить сообщение в буфер
        и запланировать delayed processing через apply_async.
        """
        from bot.handlers import messages as messages_handler

        fake_message = MagicMock()
        fake_message.message_id = 101
        fake_message.text = "завтра встреча"
        fake_message.date = timezone.now()
        fake_message.message_thread_id = None

        fake_message.from_user.id = 1111
        fake_message.from_user.username = "evgeny"
        fake_message.from_user.full_name = "Евгений"
        fake_message.from_user.is_bot = False

        fake_message.chat.id = -100123456
        fake_message.chat.title = "Тестовый чат"
        fake_message.chat.type = "supergroup"
        fake_message.chat.is_forum = None

        monkeypatch.setattr(
            messages_handler.message_buffer,
            "add_message",
            MagicMock(return_value=1),  # первое сообщение в батче
        )

        with patch.object(messages_handler.process_target_buffer, "apply_async") as apply_async_mock:
            with patch.object(messages_handler.process_target_buffer, "delay") as delay_mock:
                async_to_sync(messages_handler.handle_text_message)(fake_message)

        chat = TelegramChat.objects.get(chat_id=-100123456)
        assert chat.type == "supergroup"
        assert chat.is_forum is False

        user = TelegramUser.objects.get(telegram_id=1111)
        assert user.username == "evgeny"

        topic = Topic.objects.get(chat=chat)
        assert topic.thread_id == 0

        db_msg = DBMessage.objects.get(chat=chat, author=user)
        assert db_msg.text == "завтра встреча"

        apply_async_mock.assert_called_once_with(
            args=[-100123456, 0],
            countdown=messages_handler.BATCH_FLUSH_DELAY_SEC,
        )
        delay_mock.assert_not_called()

    def test_existing_chat_keeps_false_when_telegram_sends_none_is_forum(self, monkeypatch):
        """
        Если чат уже существует с is_forum=False,
        а новый апдейт пришёл с is_forum=None,
        поле не должно стать None.
        """
        from bot.handlers import messages as messages_handler

        existing_chat = TelegramChat.objects.create(
            chat_id=-100999888,
            title="Старое название",
            type="supergroup",
            is_forum=False,
        )

        fake_message = MagicMock()
        fake_message.message_id = 202
        fake_message.text = "ещё одно сообщение"
        fake_message.date = timezone.now()
        fake_message.message_thread_id = None

        fake_message.from_user.id = 2222
        fake_message.from_user.username = "tester"
        fake_message.from_user.full_name = "Test User"
        fake_message.from_user.is_bot = False

        fake_message.chat.id = existing_chat.chat_id
        fake_message.chat.title = "Новое название чата"
        fake_message.chat.type = "supergroup"
        fake_message.chat.is_forum = None

        monkeypatch.setattr(
            messages_handler.message_buffer,
            "add_message",
            MagicMock(return_value=1),  # первое сообщение в батче
        )

        with patch.object(messages_handler.process_target_buffer, "apply_async") as apply_async_mock:
            with patch.object(messages_handler.process_target_buffer, "delay") as delay_mock:
                async_to_sync(messages_handler.handle_text_message)(fake_message)

        existing_chat.refresh_from_db()

        assert existing_chat.title == "Новое название чата"
        assert existing_chat.type == "supergroup"
        assert existing_chat.is_forum is False

        apply_async_mock.assert_called_once_with(
            args=[existing_chat.chat_id, 0],
            countdown=messages_handler.BATCH_FLUSH_DELAY_SEC,
        )
        delay_mock.assert_not_called()