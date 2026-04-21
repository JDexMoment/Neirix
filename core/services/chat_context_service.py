import logging
import uuid
from typing import Optional, List
from django.core.cache import cache
from core.models import TelegramChat, Topic, TelegramUser, UserRole, Message

logger = logging.getLogger(__name__)


class ChatContextService:
    """Сервис для управления привязкой чатов и получением контекста"""

    def generate_link_code(self, chat: TelegramChat) -> str:
        """Генерирует или возвращает существующий код привязки чата"""
        if not chat.link_code:
            chat.link_code = uuid.uuid4()
            chat.save()
        return str(chat.link_code)

    def link_chat_to_user(self, user: TelegramUser, code: str) -> bool:
        """Привязывает чат к пользователю по коду"""
        try:
            # Ищем чат по коду (UUID в строке)
            chat = TelegramChat.objects.get(link_code=uuid.UUID(code))
        except (TelegramChat.DoesNotExist, ValueError):
            logger.warning(f"Invalid link code {code} from user {user}")
            return False

        # Проверяем, есть ли уже роль у пользователя в этом чате
        role, created = UserRole.objects.get_or_create(
            user=user,
            chat=chat,
            defaults={'role': 'member'}
        )
        if not created:
            # Уже привязан
            return True

        logger.info(f"User {user} linked to chat {chat}")
        return True

    def get_user_linked_chats(self, user: TelegramUser) -> List[TelegramChat]:
        """Возвращает список чатов, к которым пользователь привязан"""
        roles = UserRole.objects.filter(user=user).select_related('chat')
        return [role.chat for role in roles]

    def get_chat_context_for_user(self, user: TelegramUser, chat: TelegramChat, topic_id: Optional[int] = None, limit: int = 50) -> List[Message]:
        """Получает последние сообщения из чата/темы для пользователя (проверяет права)"""
        if not UserRole.objects.filter(user=user, chat=chat).exists():
            return []

        filters = {'chat': chat}
        if topic_id:
            try:
                topic = Topic.objects.get(chat=chat, thread_id=topic_id)
                filters['topic'] = topic
            except Topic.DoesNotExist:
                pass

        return Message.objects.filter(**filters).select_related('author').order_by('-timestamp')[:limit]

    def get_chat_context(self, chat: TelegramChat, topic: Optional[Topic] = None, limit: int = 50) -> List[Message]:
        """Получает последние сообщения чата/темы (без проверки прав)"""
        filters = {'chat': chat}
        if topic:
            filters['topic'] = topic
        return Message.objects.filter(**filters).select_related('author').order_by('-timestamp')[:limit]