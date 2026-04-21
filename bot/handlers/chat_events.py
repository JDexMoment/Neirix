import uuid
from asgiref.sync import sync_to_async
from aiogram import Router
from aiogram.types import ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from bot import db_utils

router = Router()


@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_bot_added_to_chat(event: ChatMemberUpdated):
    chat = await sync_to_async(db_utils.get_or_create_chat_sync)(
        chat_id=event.chat.id,
        title=event.chat.title or '',
        chat_type=event.chat.type,
        is_forum=bool(getattr(event.chat, 'is_forum', False))
    )

    if not chat.link_code:
        chat.link_code = uuid.uuid4()
        await sync_to_async(chat.save)(update_fields=['link_code'])

    await event.bot.send_message(
        chat_id=event.chat.id,
        text=(
            f"👋 Привет! Я Neirix — ваш рабочий ассистент.\n\n"
            f"Я помогу вести саммари обсуждений, отслеживать задачи и напоминать о встречах.\n\n"
            f"Чтобы участники могли привязать этот чат к личным сообщениям, используйте команду /link_chat.\n"
            f"Или сразу передайте им этот код: <code>{chat.link_code}</code>\n\n"
            f"Подробнее: /help"
        ),
        parse_mode="HTML"
    )