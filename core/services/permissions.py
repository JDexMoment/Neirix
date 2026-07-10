"""
Единая функция проверки прав на создание задач/встреч.

Роли:
  admin   — полный доступ, может управлять ролями
  manager — может создавать задачи и встречи
  member  — только чтение (его сообщения не идут в LLM)
"""
import logging
from typing import Optional

from core.models import TelegramUser, TelegramChat, Message, UserRole, Topic

logger = logging.getLogger(__name__)

ALLOWED_CREATOR_ROLES = frozenset({"manager", "admin"})


def _get_effective_chat(message: Message) -> Optional[TelegramChat]:
    """
    Определяет чат, по которому проверять права.
    Для приватных сообщений — привязанная группа.
    Для групповых — сам чат.
    """
    chat = message.chat
    if chat.type != "private":
        return chat

    author = message.author
    if not author:
        return None

    role = (
        UserRole.objects.filter(user=author)
        .select_related("chat")
        .first()
    )
    if not role:
        return None
    return role.chat


def user_can_create(message: Message) -> bool:
    """
    Может ли автор сообщения создавать задачи/встречи в этом чате?
    """
    chat = _get_effective_chat(message)
    if not chat:
        return False

    author = message.author
    if not author:
        return False

    user_role = (
        UserRole.objects.filter(user=author, chat=chat)
        .values_list("role", flat=True)
        .first()
    )
    if not user_role:
        return False

    return user_role in ALLOWED_CREATOR_ROLES


def user_can_manage_roles(author: TelegramUser, chat: TelegramChat) -> bool:
    """Может ли пользователь управлять ролями в чате."""
    return UserRole.objects.filter(
        user=author, chat=chat, role="admin"
    ).exists()
