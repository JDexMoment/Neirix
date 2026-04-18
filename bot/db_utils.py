import uuid
from typing import Optional, Tuple
import logging
from core.models import TelegramChat, Topic, TelegramUser, UserRole

logger = logging.getLogger(__name__)

def get_chat_context_sync(
    telegram_user_id: int,
    username: str,
    full_name: str,
    is_bot: bool,
    chat_type: str,
    chat_id: Optional[int] = None,
    chat_title: Optional[str] = None,
    is_forum: bool = False,
    message_thread_id: Optional[int] = None
) -> Tuple[Optional[TelegramChat], Optional[Topic], TelegramUser, Optional[str]]:
    """Синхронное определение контекста чата."""
    logger.info(f"sync: user {telegram_user_id}, chat_type={chat_type}, chat_id={chat_id}")
    db_user, _ = TelegramUser.objects.get_or_create(
        telegram_id=telegram_user_id,
        defaults={
            'username': username,
            'full_name': full_name,
            'is_bot': is_bot
        }
    )

    if chat_type in ["group", "supergroup"]:
        logger.info("sync: group chat detected")
        chat, _ = TelegramChat.objects.get_or_create(
            chat_id=chat_id,
            defaults={
                'title': chat_title or '',
                'type': chat_type,
                'is_forum': is_forum
            }
        )
        topic = None
        if is_forum and message_thread_id:
            topic, _ = Topic.objects.get_or_create(
                chat=chat,
                thread_id=message_thread_id,
                defaults={'is_active': True}
            )
        return chat, topic, db_user, None

    else:
        logger.info("sync: private chat")
        linked_roles = UserRole.objects.filter(user=db_user).select_related('chat')
        if not linked_roles.exists():
            logger.warning("sync: no linked chats")
            error_msg = (
                "У вас нет привязанных рабочих чатов.\n\n"
                "Чтобы я мог работать с контекстом группы, выполните два шага:\n"
                "1. Добавьте меня в группу и выдайте права администратора.\n"
                "2. В группе отправьте команду /link_chat — я пришлю код.\n"
                "3. Скопируйте код и отправьте его сюда, в личные сообщения.\n\n"
                "После этого вам станут доступны команды /summary, /task и /meetings."
            )
            return None, None, db_user, error_msg

        chat = linked_roles.first().chat
        logger.info(f"sync: using linked chat {chat.id}")
        return chat, None, db_user, None


def get_or_create_chat_sync(chat_id: int, title: str, chat_type: str, is_forum: bool = False) -> TelegramChat:
    is_forum = bool(is_forum)  # гарантируем True/False
    chat, _ = TelegramChat.objects.get_or_create(
        chat_id=chat_id,
        defaults={
            'title': title or '',
            'type': chat_type,
            'is_forum': is_forum
        }
    )
    return chat


def get_chat_by_link_code_sync(code: str) -> Optional[TelegramChat]:
    try:
        return TelegramChat.objects.get(link_code=uuid.UUID(code))
    except (TelegramChat.DoesNotExist, ValueError):
        return None


def get_or_create_user_sync(telegram_id: int, username: str, full_name: str, is_bot: bool) -> TelegramUser:
    """Получает или создаёт пользователя."""
    user, _ = TelegramUser.objects.get_or_create(
        telegram_id=telegram_id,
        defaults={
            'username': username,
            'full_name': full_name,
            'is_bot': is_bot
        }
    )
    return user


def create_user_role_sync(user: TelegramUser, chat: TelegramChat) -> bool:
    _, created = UserRole.objects.get_or_create(
        user=user,
        chat=chat,
        defaults={'role': 'member'}
    )
    return created