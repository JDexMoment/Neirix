"""
Тесты для команды /role: парсинг, вывод ролей, назначение.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from asgiref.sync import sync_to_async


class TestRoleCommandParsing:
    """Проверяет разбор текста команды /role."""

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_role_show_self(self):
        """/role → показывает свою роль."""
        from bot.handlers.roles import cmd_role
        from core.models import TelegramChat, TelegramUser, UserRole

        # Сетап
        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-800, title="Test Chat", type="supergroup",
        )
        user = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=800, username="testuser", full_name="Test User",
        )
        await sync_to_async(UserRole.objects.create)(
            user=user, chat=chat, role="admin",
        )

        message = MagicMock()
        message.text = "/role"
        message.chat.id = -800
        message.chat.type = "supergroup"
        message.from_user.id = 800
        message.from_user.username = "testuser"
        message.from_user.full_name = "Test User"
        message.from_user.is_bot = False
        message.answer = AsyncMock()
        message.message_thread_id = None
        message.chat.is_forum = False
        message.chat.title = "Test Chat"

        with patch("bot.handlers.roles.get_chat_context",
                   return_value=(chat, None, user)):
            await cmd_role(message)

        message.answer.assert_called_once()
        text = message.answer.call_args[0][0]
        assert "Администратор" in text
        assert "Test Chat" in text

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_role_list(self):
        """/role list → показывает всех участников."""
        from bot.handlers.roles import cmd_role
        from core.models import TelegramChat, TelegramUser, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-801, title="Team", type="supergroup",
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=801, username="boss", full_name="Boss",
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=802, username="user", full_name="User",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )

        message = MagicMock()
        message.text = "/role list"
        message.chat.id = -801
        message.chat.type = "supergroup"
        message.from_user.id = 801
        message.from_user.username = "boss"
        message.from_user.full_name = "Boss"
        message.from_user.is_bot = False
        message.answer = AsyncMock()
        message.message_thread_id = None
        message.chat.is_forum = False
        message.chat.title = "Team"

        with patch("bot.handlers.roles.get_chat_context",
                   return_value=(chat, None, admin)):
            await cmd_role(message)

        message.answer.assert_called_once()
        text = message.answer.call_args[0][0]
        assert "Участники чата Team" in text
        assert "@boss" in text
        assert "@user" in text
        assert "Администратор" in text
        assert "Участник" in text

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_role_set_by_admin(self):
        """/role set @user manager — admin повышает member до manager."""
        from bot.handlers.roles import cmd_role
        from core.models import TelegramChat, TelegramUser, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-802, title="Team", type="supergroup",
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=810, username="boss", full_name="Boss",
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=811, username="regular", full_name="Regular",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )

        message = MagicMock()
        message.text = "/role set @regular manager"
        message.chat.id = -802
        message.chat.type = "supergroup"
        message.from_user.id = 810
        message.from_user.username = "boss"
        message.from_user.full_name = "Boss"
        message.from_user.is_bot = False
        message.answer = AsyncMock()
        message.message_thread_id = None
        message.chat.is_forum = False
        message.chat.title = "Team"

        with patch("bot.handlers.roles.get_chat_context",
                   return_value=(chat, None, admin)):
            await cmd_role(message)

        message.answer.assert_called_once()
        text = message.answer.call_args[0][0]
        assert "изменена" in text or "назначена" in text
        assert "Менеджер" in text

        # Проверим, что роль в БД действительно изменилась
        updated_role = await sync_to_async(
            lambda: UserRole.objects.get(user=member, chat=chat)
        )()
        assert updated_role.role == "manager"

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_role_set_denied_for_manager(self):
        """/role set @user manager — manager НЕ может назначать."""
        from bot.handlers.roles import cmd_role
        from core.models import TelegramChat, TelegramUser, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-803, title="Team", type="supergroup",
        )
        manager = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=820, username="manager", full_name="Manager",
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=821, username="user", full_name="User",
        )
        await sync_to_async(UserRole.objects.create)(
            user=manager, chat=chat, role="manager",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )

        message = MagicMock()
        message.text = "/role set @user manager"
        message.chat.id = -803
        message.chat.type = "supergroup"
        message.from_user.id = 820
        message.from_user.username = "manager"
        message.from_user.full_name = "Manager"
        message.from_user.is_bot = False
        message.answer = AsyncMock()
        message.message_thread_id = None
        message.chat.is_forum = False
        message.chat.title = "Team"

        with patch("bot.handlers.roles.get_chat_context",
                   return_value=(chat, None, manager)):
            await cmd_role(message)

        message.answer.assert_called_once()
        assert "недостаточно прав" in message.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_role_set_invalid_role(self):
        """/role set @user king → ошибка (нет такой роли)."""
        from bot.handlers.roles import cmd_role
        from core.models import TelegramChat, TelegramUser, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-804, title="Team", type="supergroup",
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=830, username="boss", full_name="Boss",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )

        message = MagicMock()
        message.text = "/role set @boss king"
        message.chat.id = -804
        message.chat.type = "supergroup"
        message.from_user.id = 830
        message.from_user.username = "boss"
        message.from_user.full_name = "Boss"
        message.from_user.is_bot = False
        message.answer = AsyncMock()
        message.message_thread_id = None
        message.chat.is_forum = False
        message.chat.title = "Team"

        with patch("bot.handlers.roles.get_chat_context",
                   return_value=(chat, None, admin)):
            await cmd_role(message)

        message.answer.assert_called_once()
        assert "Некорректная роль" in message.answer.call_args[0][0]

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_role_user_not_found(self):
        """/role set @unknown manager → пользователь не найден."""
        from bot.handlers.roles import cmd_role
        from core.models import TelegramChat, TelegramUser, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-805, title="Team", type="supergroup",
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=840, username="boss", full_name="Boss",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )

        message = MagicMock()
        message.text = "/role set @ghost manager"
        message.chat.id = -805
        message.chat.type = "supergroup"
        message.from_user.id = 840
        message.from_user.username = "boss"
        message.from_user.full_name = "Boss"
        message.from_user.is_bot = False
        message.answer = AsyncMock()
        message.message_thread_id = None
        message.chat.is_forum = False
        message.chat.title = "Team"

        with patch("bot.handlers.roles.get_chat_context",
                   return_value=(chat, None, admin)):
            await cmd_role(message)

        message.answer.assert_called_once()
        assert "не найден" in message.answer.call_args[0][0].lower()
