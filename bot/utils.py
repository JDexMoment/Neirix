from asgiref.sync import sync_to_async
from aiogram.types import Message
from typing import Optional, Tuple
from core.models import TelegramChat, Topic, TelegramUser
from . import db_utils


async def get_chat_context(message: Message) -> Tuple[Optional[TelegramChat], Optional[Topic], Optional[TelegramUser]]:
    chat, topic, db_user, error_msg = await sync_to_async(db_utils.get_chat_context_sync)(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username or '',
        full_name=message.from_user.full_name,
        is_bot=message.from_user.is_bot,
        chat_type=message.chat.type,
        chat_id=message.chat.id if message.chat.id else None,
        chat_title=message.chat.title or '',
        is_forum=getattr(message.chat, 'is_forum', False),
        message_thread_id=message.message_thread_id if hasattr(message, 'message_thread_id') else None
    )

    if error_msg:
        await message.answer(error_msg)
        return None, None, db_user

    return chat, topic, db_user