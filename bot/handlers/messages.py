import logging

from aiogram import Router, F
from aiogram.types import Message
from asgiref.sync import sync_to_async

from core.models import Message as DBMessage, TelegramChat, Topic, TelegramUser, UserRole
from core.services.message_buffer import MessageBuffer, MAX_BATCH_SIZE
from celery_app.tasks.process_messages import process_target_buffer

router = Router()
logger = logging.getLogger(__name__)
message_buffer = MessageBuffer()

BATCH_FLUSH_DELAY_SEC = 30


def _extract_is_forum(chat) -> bool:
    if chat.type == "private":
        return False
    return bool(getattr(chat, "is_forum", False))


def _can_create_in_chat(user: TelegramUser, chat: TelegramChat) -> bool:
    target_chat = chat
    if chat.type == "private":
        role = (
            UserRole.objects.filter(user=user)
            .select_related("chat")
            .first()
        )
        if not role:
            return False
        target_chat = role.chat
    user_role = (
        UserRole.objects.filter(user=user, chat=target_chat)
        .values_list("role", flat=True)
        .first()
    )
    return user_role in ("manager", "admin")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message):
    """
    Сохраняет входящее текстовое сообщение в БД.
    В ЛС — сначала проверяет NLP-запрос.
    Если это ответ на сообщение бота с задачей/встречей — создаёт комментарий.
    """

    # ═══ Если пользователь сейчас вводит подзадачу/исполнителя — не обрабатываем как задачу ═══
    from bot.handlers.subtasks import PENDING
    if message.from_user.id in PENDING:
        return

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
        return db_msg, db_user, chat

    db_message, db_user, chat = await save_message()

    # ═══ Проверяем, не является ли это комментарием к задаче/встрече ═══
    # Если пользователь ответил на сообщение бота, в котором есть **название**
    if message.reply_to_message and message.reply_to_message.text:
        from bot.handlers.comments import _extract_title_from_msg, _find_task_by_title, _find_meeting_by_title, _add_comment
        replied_text = message.reply_to_message.text
        title = _extract_title_from_msg(replied_text)
        if title:
            comment_text = message.text.strip()
            if len(comment_text) >= 2:
                chat_id_num = chat.chat_id
                task = await _find_task_by_title(chat_id_num, title)
                meeting = None
                if not task:
                    meeting = await _find_meeting_by_title(chat_id_num, title)
                if task or meeting:
                    kwargs = {"author_id": message.from_user.id, "text": comment_text}
                    if task:
                        kwargs["task_id"] = task.id
                    elif meeting:
                        kwargs["meeting_id"] = meeting.id
                    result = await _add_comment(**kwargs)
                    if result:
                        target = "задачи" if task else "встречи"
                        await message.reply(f"💬 Комментарий добавлен к {target} «{title}»")
                        logger.info("Comment added via reply: %s → %s", db_user, title)
                    return  # не буферизируем комментарий

    # ═══ В ЛС — через Celery (не блокируем бота) ═══
    if message.chat.type == "private":
        from celery_app.tasks.nlp_processing import process_nlp_message
        process_nlp_message.delay(
            chat_id=message.chat.id,
            user_telegram_id=message.from_user.id,
            text=message.text or "",
            db_message_id=db_message.id,
            telegram_msg_id=message.message_id,
            message_thread_id=message.message_thread_id or 0,
        )
        return

    # ═══ Проверка прав: только manager/admin попадают в буфер ═══
    can_create = await sync_to_async(_can_create_in_chat)(db_user, chat)

    if not can_create:
        logger.info(
            "Member skipped from buffer | user=%s chat=%s text=%r",
            db_user, chat.chat_id, db_message.text[:100],
        )
        return

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
