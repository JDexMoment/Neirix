import logging
from celery import shared_task
from django.utils import timezone
from core.models import Message, TelegramChat, Topic, TelegramUser
from core.services.task_service import TaskService
from core.services.meeting_service import MeetingService
from vector_store.client import VectorStoreClient
from vector_store.embeddings import generate_embedding

logger = logging.getLogger(__name__)


@shared_task
def process_new_message(message_id: int):
    """Обрабатывает новое сообщение: генерирует эмбеддинг, сохраняет в Qdrant, извлекает сущности"""
    try:
        message = Message.objects.select_related('chat', 'topic', 'author').get(id=message_id)
    except Message.DoesNotExist:
        logger.error(f"Message {message_id} not found")
        return

    # Генерируем эмбеддинг и сохраняем в Qdrant
    try:
        embedding = generate_embedding(message.text)
        vector_client = VectorStoreClient()
        payload = {
            "message_id": message.id,
            "chat_id": message.chat.chat_id,
            "topic_id": message.topic.thread_id if message.topic else None,
            "user_id": message.author.telegram_id,
            "username": message.author.username,
            "timestamp": int(message.timestamp.timestamp()),
            "text": message.text[:1000],  # ограничиваем длину
        }
        vector_client.upsert_message(message.id, embedding, payload)
    except Exception as e:
        logger.error(f"Failed to generate/save embedding for message {message_id}: {e}")
        # Продолжаем выполнение, чтобы извлечь сущности

    # Извлекаем задачи и встречи через LLM
    task_service = TaskService()
    meeting_service = MeetingService()

    try:
        tasks = task_service.extract_tasks_from_message(message)
        if tasks:
            logger.info(f"Extracted {len(tasks)} tasks from message {message_id}")
    except Exception as e:
        logger.error(f"Task extraction failed for message {message_id}: {e}")

    try:
        meeting = meeting_service.extract_meeting_from_message(message)
        if meeting:
            logger.info(f"Extracted meeting from message {message_id}")
    except Exception as e:
        logger.error(f"Meeting extraction failed for message {message_id}: {e}")

    # Отмечаем сообщение как обработанное
    message.is_processed = True
    message.save()


@shared_task
def process_new_messages_batch():
    """Периодическая задача: находит необработанные сообщения и ставит их в очередь"""
    unprocessed = Message.objects.filter(is_processed=False).order_by('timestamp')[:100]
    for message in unprocessed:
        process_new_message.delay(message.id)
    logger.info(f"Queued {unprocessed.count()} messages for processing")