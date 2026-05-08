import asyncio
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Безопасный запуск async-кода из синхронной Celery-задачи."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@shared_task(name="celery_app.tasks.process_messages.process_target_buffer")
def process_target_buffer(chat_id: int, topic_id: int):
    """
    Обрабатывает конкретный буфер сообщений:
    - либо по таймеру,
    - либо немедленно при достижении MAX_BATCH_SIZE.
    """
    return _run_async(_process_target_buffer_async(chat_id, topic_id))


async def _process_target_buffer_async(chat_id: int, topic_id: int):
    from core.services.message_buffer import MessageBuffer
    from core.services.batch_processor import BatchProcessor

    buffer = MessageBuffer()

    messages = buffer.flush(chat_id, topic_id)

    if not messages:
        logger.debug(
            "Buffer already empty, skipping | chat=%s topic=%s",
            chat_id,
            topic_id,
        )
        return {"tasks": 0, "meetings": 0, "status": "skipped"}

    logger.info(
        "Processing target buffer | chat=%s topic=%s size=%s",
        chat_id,
        topic_id,
        len(messages),
    )

    processor = BatchProcessor()
    result = await processor.process_batch(chat_id, topic_id, messages)

    logger.info(
        "Buffer processed | chat=%s topic=%s tasks=%s meetings=%s",
        chat_id,
        topic_id,
        result["tasks_created"],
        result["meetings_created"],
    )

    return result