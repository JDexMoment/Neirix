import uuid
from asgiref.sync import sync_to_async
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from bot import db_utils

router = Router()


@router.message(Command("link_chat"))
async def cmd_link_chat(message: Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("Эта команда предназначена только для групповых чатов.")
        return

    chat = await sync_to_async(db_utils.get_or_create_chat_sync)(
        chat_id=message.chat.id,
        title=message.chat.title or '',
        chat_type=message.chat.type,
        is_forum=bool(getattr(message.chat, 'is_forum', False))
    )

    if not chat.link_code:
        chat.link_code = uuid.uuid4()
        await sync_to_async(chat.save)(update_fields=['link_code'])

    await message.answer(
        f"Код привязки чата: <code>{chat.link_code}</code>\n\n"
        f"Отправьте этот код мне в личные сообщения, чтобы получить доступ к контексту этого чата.",
        parse_mode="HTML"
    )


@router.message(F.chat.type == "private", F.text.regexp(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"))
async def process_link_code(message: Message):
    code = message.text.strip()

    chat = await sync_to_async(db_utils.get_chat_by_link_code_sync)(code)
    if not chat:
        await message.answer("Неверный код привязки. Проверьте и попробуйте снова.")
        return

    db_user = await sync_to_async(db_utils.get_or_create_user_sync)(
        telegram_id=message.from_user.id,
        username=message.from_user.username or '',
        full_name=message.from_user.full_name,
        is_bot=message.from_user.is_bot
    )

    created = await sync_to_async(db_utils.create_user_role_sync)(db_user, chat)

    if created:
        await message.answer(f"Чат <b>{chat.title}</b> успешно привязан! Теперь я буду использовать его контекст для команд.")
    else:
        await message.answer(f"ℹЧат <b>{chat.title}</b> уже был привязан ранее.")