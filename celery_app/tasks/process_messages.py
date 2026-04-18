import asyncio
import logging
from celery import shared_task
from core.models import Message
from core.services.task_service import TaskService
from core.services.meeting_service import MeetingService
from vector_store.client import VectorStoreClient
from vector_store.embeddings import generate_embedding

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Безопасный запуск асинхронной функции из синхронного контекста Celery."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@shared_task
def process_new_message(message_id: int):
    try:
        message = Message.objects.select_related('chat', 'topic', 'author').get(id=message_id)
    except Message.DoesNotExist:
        logger.error(f"Message {message_id} not found")
        return

    # 1. Эмбеддинг
    try:
        embedding = _run_async(generate_embedding(message.text))
        vector_client = VectorStoreClient()
        payload = {
            "message_id": message.id,
            "chat_id": message.chat.chat_id,
            "topic_id": message.topic.thread_id if message.topic else None,
            "user_id": message.author.telegram_id,
            "username": message.author.username,
            "timestamp": int(message.timestamp.timestamp()),
            "text": message.text[:1000],
        }
        vector_client.upsert_message(message.id, embedding, payload)
    except Exception as e:
        logger.error(f"Embedding failed for message {message_id}: {e}")

    # 2. Извлечение задач и встреч
    task_service = TaskService()
    meeting_service = MeetingService()

    try:
        tasks = _run_async(task_service.extract_tasks_from_message(message))
        if tasks:
            logger.info(f"Extracted {len(tasks)} tasks from message {message_id}")
    except Exception as e:
        logger.error(f"Task extraction failed: {e}")

    try:
        meeting = _run_async(meeting_service.extract_meeting_from_message(message))
        if meeting:
            logger.info(f"Extracted meeting from message {message_id}")
    except Exception as e:
        logger.error(f"Meeting extraction failed: {e}")

    message.is_processed = True
    message.save()