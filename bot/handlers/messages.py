from aiogram import Router, F
from aiogram.types import Message
from asgiref.sync import sync_to_async
from core.models import Message as DBMessage, TelegramChat, Topic, TelegramUser
from celery_app.tasks.process_messages import process_new_message

router = Router()


@router.message(F.text)
async def handle_text_message(message: Message):
    """Сохраняет входящее текстовое сообщение и ставит задачу на обработку."""
    # Сохраняем сообщение в БД (синхронно через sync_to_async)
    @sync_to_async
    def save_message():
        # Получаем или создаём пользователя
        db_user, _ = TelegramUser.objects.get_or_create(
            telegram_id=message.from_user.id,
            defaults={
                'username': message.from_user.username or '',
                'full_name': message.from_user.full_name,
                'is_bot': message.from_user.is_bot
            }
        )

        # Получаем или создаём чат
        chat, _ = TelegramChat.objects.get_or_create(
            chat_id=message.chat.id,
            defaults={
                'title': message.chat.title or '',
                'type': message.chat.type,
                'is_forum': getattr(message.chat, 'is_forum', False)
            }
        )

        # Определяем тему, если это форум
        topic = None
        if chat.is_forum and message.message_thread_id:
            topic, _ = Topic.objects.get_or_create(
                chat=chat,
                thread_id=message.message_thread_id,
                defaults={'is_active': True}
            )

        # Создаём запись сообщения
        db_msg = DBMessage.objects.create(
            telegram_msg_id=message.message_id,
            chat=chat,
            topic=topic,
            author=db_user,
            text=message.text or '',
            timestamp=message.date,
            is_processed=False
        )
        return db_msg

    db_message = await save_message()

    # Отправляем задачу в Celery на обработку (извлечение задач, встреч, эмбеддингов)
    process_new_message.delay(db_message.id)