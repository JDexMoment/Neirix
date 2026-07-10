"""
Тесты для ролевой системы:
- permissions.py (проверка прав)
- db_utils.py (первый пользователь → admin)
- Сквозные тесты (member не создаёт задачи/встречи)
"""
import pytest
from datetime import timedelta
from django.utils import timezone
from asgiref.sync import sync_to_async


# ══════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════

def _make_naive_date_str(days_ahead=2):
    from datetime import datetime as dt_mod
    naive = dt_mod.now() + timedelta(days=days_ahead)
    return naive.strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════════
# ТЕСТЫ: core.services.permissions
# ══════════════════════════════════════════════════════════════════

class TestPermissions:
    """Проверка функций user_can_create и user_can_manage_roles."""

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_admin_can_create(self):
        """admin → может создавать задачи/встречи."""
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole
        from core.services.permissions import user_can_create

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-100, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=1, username="boss", full_name="Boss",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=1, chat=chat, topic=topic,
            author=admin, text="задача", timestamp=timezone.now(),
        )

        can = await sync_to_async(user_can_create)(msg)
        assert can is True

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_manager_can_create(self):
        """manager → может создавать задачи/встречи."""
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole
        from core.services.permissions import user_can_create

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-101, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        manager = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=2, username="manager", full_name="Manager",
        )
        await sync_to_async(UserRole.objects.create)(
            user=manager, chat=chat, role="manager",
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=2, chat=chat, topic=topic,
            author=manager, text="задача", timestamp=timezone.now(),
        )

        can = await sync_to_async(user_can_create)(msg)
        assert can is True

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_member_cannot_create(self):
        """member → НЕ может создавать задачи/встречи."""
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole
        from core.services.permissions import user_can_create

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-102, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=3, username="user", full_name="User",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=3, chat=chat, topic=topic,
            author=member, text="задача", timestamp=timezone.now(),
        )

        can = await sync_to_async(user_can_create)(msg)
        assert can is False

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_user_without_role_cannot_create(self):
        """Пользователь без роли в чате → НЕ может создавать."""
        from core.models import TelegramChat, TelegramUser, Topic, Message
        from core.services.permissions import user_can_create

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-103, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        user = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=4, username="stranger", full_name="Stranger",
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=4, chat=chat, topic=topic,
            author=user, text="задача", timestamp=timezone.now(),
        )

        can = await sync_to_async(user_can_create)(msg)
        assert can is False

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_private_chat_with_linked_group_admin(self):
        """Из ЛС, привязан к группе как admin → может создавать."""
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole
        from core.services.permissions import user_can_create

        group = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-200, title="Group", type="supergroup",
        )
        await sync_to_async(Topic.objects.create)(
            chat=group, thread_id=0,
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=10, username="bigboss", full_name="Big Boss",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=group, role="admin",
        )
        private = await sync_to_async(TelegramChat.objects.create)(
            chat_id=100200, title="", type="private",
        )
        private_topic = await sync_to_async(Topic.objects.create)(
            chat=private, thread_id=0,
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=10, chat=private, topic=private_topic,
            author=admin, text="задача", timestamp=timezone.now(),
        )

        can = await sync_to_async(user_can_create)(msg)
        assert can is True

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_private_chat_with_linked_group_member(self):
        """Из ЛС, привязан как member → НЕ может создавать."""
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole
        from core.services.permissions import user_can_create

        group = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-201, title="Group", type="supergroup",
        )
        await sync_to_async(Topic.objects.create)(
            chat=group, thread_id=0,
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=11, username="peon", full_name="Peon",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=group, role="member",
        )
        private = await sync_to_async(TelegramChat.objects.create)(
            chat_id=100201, title="", type="private",
        )
        private_topic = await sync_to_async(Topic.objects.create)(
            chat=private, thread_id=0,
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=11, chat=private, topic=private_topic,
            author=member, text="задача", timestamp=timezone.now(),
        )

        can = await sync_to_async(user_can_create)(msg)
        assert can is False

    # ── user_can_manage_roles ────────────────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_admin_can_manage_roles(self):
        """admin → может управлять ролями."""
        from core.models import TelegramChat, TelegramUser, UserRole
        from core.services.permissions import user_can_manage_roles

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-300, title="Test", type="supergroup",
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=20, username="boss", full_name="Boss",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )

        can = await sync_to_async(user_can_manage_roles)(admin, chat)
        assert can is True

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_manager_cannot_manage_roles(self):
        """manager → НЕ может управлять ролями."""
        from core.models import TelegramChat, TelegramUser, UserRole
        from core.services.permissions import user_can_manage_roles

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-301, title="Test", type="supergroup",
        )
        manager = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=21, username="manager", full_name="Manager",
        )
        await sync_to_async(UserRole.objects.create)(
            user=manager, chat=chat, role="manager",
        )

        can = await sync_to_async(user_can_manage_roles)(manager, chat)
        assert can is False

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_member_cannot_manage_roles(self):
        """member → НЕ может управлять ролями."""
        from core.models import TelegramChat, TelegramUser, UserRole
        from core.services.permissions import user_can_manage_roles

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-302, title="Test", type="supergroup",
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=22, username="user", full_name="User",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )

        can = await sync_to_async(user_can_manage_roles)(member, chat)
        assert can is False


# ══════════════════════════════════════════════════════════════════
# ТЕСТЫ: bot.db_utils — первый пользователь становится admin
# ══════════════════════════════════════════════════════════════════

class TestFirstUserBecomesAdmin:
    """Первый, кто вводит код привязки → admin, остальные → member."""

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_first_user_becomes_admin(self):
        from bot.db_utils import create_user_role_sync
        from core.models import TelegramChat, TelegramUser, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-400, title="Test", type="supergroup",
        )
        user1 = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=30, username="first", full_name="First",
        )

        created = await sync_to_async(create_user_role_sync)(user1, chat)
        assert created is True

        role = await sync_to_async(
            lambda: UserRole.objects.get(user=user1, chat=chat)
        )()
        assert role.role == "admin"

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_second_user_becomes_member(self):
        from bot.db_utils import create_user_role_sync
        from core.models import TelegramChat, TelegramUser, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-401, title="Test", type="supergroup",
        )
        user1 = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=31, username="first", full_name="First",
        )
        user2 = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=32, username="second", full_name="Second",
        )

        await sync_to_async(create_user_role_sync)(user1, chat)
        await sync_to_async(create_user_role_sync)(user2, chat)

        role2 = await sync_to_async(
            lambda: UserRole.objects.get(user=user2, chat=chat)
        )()
        assert role2.role == "member"

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_repeated_link_does_not_change_role(self):
        """Повторная привязка не меняет роль."""
        from bot.db_utils import create_user_role_sync
        from core.models import TelegramChat, TelegramUser, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-402, title="Test", type="supergroup",
        )
        user = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=33, username="user", full_name="User",
        )

        await sync_to_async(create_user_role_sync)(user, chat)
        created = await sync_to_async(create_user_role_sync)(user, chat)
        assert created is False  # уже была роль, новая не создана

        role = await sync_to_async(
            lambda: UserRole.objects.get(user=user, chat=chat)
        )()
        assert role.role == "admin"  # не изменилась


# ══════════════════════════════════════════════════════════════════
# СКВОЗНЫЕ ТЕСТЫ: сервисы не создают сущности для member
# ══════════════════════════════════════════════════════════════════

class TestTaskServicePermissions:
    """TaskService блокирует создание задач для member."""

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_member_task_creation_blocked(self):
        from core.services.task_service import TaskService
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-500, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=40, username="member", full_name="Member",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=50, chat=chat, topic=topic,
            author=member, text="задача", timestamp=timezone.now(),
        )

        service = TaskService()
        task = await service._create_task_from_data(
            {"title": "Задача", "assignees": [],
             "due_date": _make_naive_date_str(), "description": ""},
            msg,
        )

        assert task is None, "member не должен создавать задачи"

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_admin_task_creation_allowed(self):
        from core.services.task_service import TaskService
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-501, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=41, username="boss", full_name="Boss",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=51, chat=chat, topic=topic,
            author=admin, text="задача", timestamp=timezone.now(),
        )

        service = TaskService()
        task = await service._create_task_from_data(
            {"title": "Задача", "assignees": [],
             "due_date": _make_naive_date_str(), "description": ""},
            msg,
        )

        assert task is not None


class TestMeetingServicePermissions:
    """MeetingService блокирует создание встреч для member."""

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_member_meeting_creation_blocked(self):
        from core.services.meeting_service import MeetingService
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-600, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=60, username="member", full_name="Member",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=60, chat=chat, topic=topic,
            author=member, text="встреча", timestamp=timezone.now(),
        )

        service = MeetingService()
        meeting = await service._create_meeting_from_data(
            {"title": "Встреча", "participants": [],
             "start_at": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00"),
             "description": ""},
            msg,
        )

        assert meeting is None, "member не должен создавать встречи"

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_manager_meeting_creation_allowed(self):
        from core.services.meeting_service import MeetingService
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-601, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        manager = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=61, username="manager", full_name="Manager",
        )
        await sync_to_async(UserRole.objects.create)(
            user=manager, chat=chat, role="manager",
        )
        msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=61, chat=chat, topic=topic,
            author=manager, text="встреча", timestamp=timezone.now(),
        )

        service = MeetingService()
        meeting = await service._create_meeting_from_data(
            {"title": "Встреча", "participants": [],
             "start_at": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00"),
             "description": ""},
            msg,
        )

        assert meeting is not None


# ══════════════════════════════════════════════════════════════════
# СКВОЗНЫЕ ТЕСТЫ: batch-обработка с ролевым фильтром
# ══════════════════════════════════════════════════════════════════

class TestBatchProcessorPermissions:
    """BatchProcessor отфильтровывает сообщения от member."""

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_member_message_skipped_in_batch(self):
        """
        Если все сообщения в батче от member → LLM не вызывается,
        tasks_created = 0.
        """
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.services.batch_processor import BatchProcessor
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-700, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=70, username="member", full_name="Member",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )
        db_msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=70, chat=chat, topic=topic,
            author=member, text="задача @user", timestamp=timezone.now(),
        )

        buffer_data = [{
            "message_id": db_msg.id,
            "text": db_msg.text,
            "author_name": member.full_name,
            "timestamp": db_msg.timestamp.timestamp(),
        }]

        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.extract_all_from_messages = AsyncMock()
            MockLLM.return_value = mock_llm

            with patch("core.services.batch_processor.user_can_create", return_value=False):
                with patch("core.services.batch_processor.generate_embeddings_batch",
                           new_callable=AsyncMock, return_value=[None]):
                    with patch("core.services.batch_processor.VectorStoreClient"):
                        processor = BatchProcessor()
                        result = await processor.process_batch(
                            chat_id=chat.chat_id,
                            topic_id=topic.thread_id,
                            messages=buffer_data,
                        )

        # LLM НЕ ДОЛЖЕН БЫТЬ ВЫЗВАН
        mock_llm.extract_all_from_messages.assert_not_called()
        assert result == {"tasks_created": 0, "meetings_created": 0}

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_admin_message_processed_in_batch(self):
        """Сообщение от admin → обрабатывается LLM."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.services.batch_processor import BatchProcessor
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-701, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=71, username="boss", full_name="Boss",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )
        db_msg = await sync_to_async(Message.objects.create)(
            telegram_msg_id=71, chat=chat, topic=topic,
            author=admin, text="задача @user", timestamp=timezone.now(),
        )

        buffer_data = [{
            "message_id": db_msg.id,
            "text": db_msg.text,
            "author_name": admin.full_name,
            "timestamp": db_msg.timestamp.timestamp(),
        }]

        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.extract_all_from_messages = AsyncMock(
                return_value={"tasks": [], "meetings": []}
            )
            MockLLM.return_value = mock_llm

            with patch("core.services.batch_processor.user_can_create", return_value=True):
                with patch("core.services.batch_processor.generate_embeddings_batch",
                           new_callable=AsyncMock, return_value=[None]):
                    with patch("core.services.batch_processor.VectorStoreClient"):
                        processor = BatchProcessor()
                        await processor.process_batch(
                            chat_id=chat.chat_id,
                            topic_id=topic.thread_id,
                            messages=buffer_data,
                        )

        # LLM ДОЛЖЕН БЫТЬ ВЫЗВАН
        mock_llm.extract_all_from_messages.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_mixed_batch_filters_member_only(self):
        """
        Батч: 1 сообщение от admin + 1 от member.
        LLM получает только 1 сообщение (от admin).
        """
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.services.batch_processor import BatchProcessor
        from core.models import TelegramChat, TelegramUser, Topic, Message, UserRole

        chat = await sync_to_async(TelegramChat.objects.create)(
            chat_id=-702, title="Test", type="supergroup",
        )
        topic = await sync_to_async(Topic.objects.create)(
            chat=chat, thread_id=0,
        )
        admin = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=80, username="boss", full_name="Boss",
        )
        member = await sync_to_async(TelegramUser.objects.create)(
            telegram_id=81, username="peon", full_name="Peon",
        )
        await sync_to_async(UserRole.objects.create)(
            user=admin, chat=chat, role="admin",
        )
        await sync_to_async(UserRole.objects.create)(
            user=member, chat=chat, role="member",
        )

        msg_admin = await sync_to_async(Message.objects.create)(
            telegram_msg_id=80, chat=chat, topic=topic,
            author=admin, text="задача @peon", timestamp=timezone.now(),
        )
        msg_member = await sync_to_async(Message.objects.create)(
            telegram_msg_id=81, chat=chat, topic=topic,
            author=member, text="я тоже хочу создать задачу",
            timestamp=timezone.now(),
        )

        buffer_data = [
            {"message_id": msg_admin.id, "text": msg_admin.text,
             "author_name": admin.full_name, "timestamp": msg_admin.timestamp.timestamp()},
            {"message_id": msg_member.id, "text": msg_member.text,
             "author_name": member.full_name,
             "timestamp": msg_member.timestamp.timestamp()},
        ]

        with patch("core.utils.llm_client.LLMClient") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.extract_all_from_messages = AsyncMock(
                return_value={"tasks": [], "meetings": []}
            )
            MockLLM.return_value = mock_llm

            with patch("core.services.batch_processor.user_can_create", side_effect=[True, False]):
                with patch("core.services.batch_processor.generate_embeddings_batch",
                           new_callable=AsyncMock, return_value=[[0.1], [0.2]]):
                    with patch("core.services.batch_processor.VectorStoreClient"):
                        processor = BatchProcessor()
                        await processor.process_batch(
                            chat_id=chat.chat_id,
                            topic_id=topic.thread_id,
                            messages=buffer_data,
                        )

        # LLM получил только 1 сообщение (от admin)
        call_args = mock_llm.extract_all_from_messages.call_args
        assert call_args is not None, "LLM должен быть вызван"
        messages_sent = call_args[0][0]
        assert len(messages_sent) == 1, (
            f"Ожидалось 1 сообщение (от admin), получено {len(messages_sent)}"
        )
        assert messages_sent[0].id == msg_admin.id

        # LLM получил только 1 сообщение (от admin)
        call_args = mock_llm.extract_all_from_messages.call_args
        assert call_args is not None, "LLM должен быть вызван"
        messages_sent = call_args[0][0]
        assert len(messages_sent) == 1, (
            f"Ожидалось 1 сообщение (от admin), получено {len(messages_sent)}"
        )
        assert messages_sent[0].id == msg_admin.id
