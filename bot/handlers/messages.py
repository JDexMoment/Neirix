import logging

from aiogram import Router, F
from aiogram.types import Message
from asgiref.sync import sync_to_async

from core.models import Message as DBMessage, TelegramChat, Topic, TelegramUser
from core.services.message_buffer import MessageBuffer, MAX_BATCH_SIZE
from celery_app.tasks.process_messages import process_target_buffer

router = Router()
logger = logging.getLogger(__name__)
message_buffer = MessageBuffer()

BATCH_FLUSH_DELAY_SEC = 30


def _extract_is_forum(chat) -> bool:
    """
    Безопасно нормализует is_forum в bool.
    Telegram/aiogram может прислать is_forum=None.
    """
    if chat.type == "private":
        return False
    return bool(getattr(chat, "is_forum", False))


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message):
    """
    Сохраняет входящее текстовое сообщение в БД
    и кладёт его в Redis-буфер для батч-обработки.
    """

    @sync_to_async
    def save_message():
        db_user, _ = TelegramUser.objects.get_or_create(
            telegram_id=message.from_user.id,
            defaults={
                "username": message.from_user.username or "",
                "full_name": message.from_user.full_name,
                "is_bot": message.from_user.is_bot,
            },
        )

        updated = False
        current_username = message.from_user.username or ""
        current_full_name = message.from_user.full_name or ""

        if db_user.username != current_username:
            db_user.username = current_username
            updated = True

        if db_user.full_name != current_full_name:
            db_user.full_name = current_full_name
            updated = True

        if db_user.is_bot != message.from_user.is_bot:
            db_user.is_bot = message.from_user.is_bot
            updated = True

        if updated:
            db_user.save(update_fields=["username", "full_name", "is_bot"])

        current_is_forum = _extract_is_forum(message.chat)

        chat, _ = TelegramChat.objects.get_or_create(
            chat_id=message.chat.id,
            defaults={
                "title": message.chat.title or "",
                "type": message.chat.type,
                "is_forum": current_is_forum,
            },
        )

        chat_updated = False
        current_title = message.chat.title or ""
        current_type = message.chat.type

        if chat.title != current_title:
            chat.title = current_title
            chat_updated = True

        if chat.type != current_type:
            chat.type = current_type
            chat_updated = True

        if chat.is_forum is None or chat.is_forum != current_is_forum:
            chat.is_forum = current_is_forum
            chat_updated = True

        if chat_updated:
            chat.save(update_fields=["title", "type", "is_forum"])

        if chat.is_forum and message.message_thread_id:
            topic, _ = Topic.objects.get_or_create(
                chat=chat,
                thread_id=message.message_thread_id,
                defaults={"is_active": True},
            )
        else:
            topic, _ = Topic.objects.get_or_create(
                chat=chat,
                thread_id=0,
                defaults={"is_active": True},
            )

        db_msg = DBMessage.objects.create(
            telegram_msg_id=message.message_id,
            chat=chat,
            topic=topic,
            author=db_user,
            text=message.text or "",
            timestamp=message.date,
            is_processed=False,
        )
        return db_msg

    db_message = await save_message()

    chat_id = db_message.chat.chat_id
    topic_id = db_message.topic.thread_id

    buffer_size = message_buffer.add_message(
        chat_id=chat_id,
        topic_id=topic_id,
        message_data={
            "message_id": db_message.id,
            "text": db_message.text,
            "author_name": (
                db_message.author.full_name
                or db_message.author.username
                or str(db_message.author.telegram_id)
            ),
            "timestamp": db_message.timestamp.timestamp(),
        },
    )

    logger.info(
        "Message buffered | chat=%s topic=%s size=%s text=%r",
        chat_id,
        topic_id,
        buffer_size,
        db_message.text[:100],
    )

    if buffer_size == 1:
        logger.info(
            "Scheduling delayed batch flush | chat=%s topic=%s delay=%s",
            chat_id,
            topic_id,
            BATCH_FLUSH_DELAY_SEC,
        )
        process_target_buffer.apply_async(
            args=[chat_id, topic_id],
            countdown=BATCH_FLUSH_DELAY_SEC,
        )

    elif buffer_size >= MAX_BATCH_SIZE:
        logger.info(
            "Batch full, triggering immediate processing | chat=%s topic=%s size=%s",
            chat_id,
            topic_id,
            buffer_size,
        )
        process_target_buffer.delay(chat_id, topic_id)