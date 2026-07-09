import logging

from asgiref.sync import sync_to_async

from core.models import Message, TaskAssignee, TelegramUser
from vector_store.client import VectorStoreClient
from vector_store.embeddings import generate_embeddings_batch

logger = logging.getLogger(__name__)


class BatchProcessor:
    def __init__(self):
        self._vector_client = None

    @property
    def vector_client(self):
        if self._vector_client is None:
            self._vector_client = VectorStoreClient()
        return self._vector_client

    async def process_batch(
        self,
        chat_id: int,
        topic_id: int,
        messages: list[dict],
    ) -> dict:
        if not messages:
            return {"tasks_created": 0, "meetings_created": 0}

        msg_ids = [m["message_id"] for m in messages]
        db_messages = await self._load_messages(msg_ids)

        if not db_messages:
            logger.warning("No DB messages found for ids=%s", msg_ids)
            return {"tasks_created": 0, "meetings_created": 0}

        await self._store_embeddings(db_messages)

        tasks_created = 0
        meetings_created = 0
        source_message = db_messages[-1]

        try:
            from core.utils.llm_client import LLMClient
            llm = LLMClient()
            result = await llm.extract_all_from_messages(db_messages)

            if result:
                for task_data in result.get("tasks", []):
                    try:
                        task = await self._create_task(task_data, source_message)
                        if task:
                            tasks_created += 1
                            logger.info("Task created | id=%s title=%s", task.id, task.title)
                    except Exception as e:
                        logger.error("Task creation failed | data=%s: %s", task_data, e, exc_info=True)

                from core.services.meeting_service import MeetingService
                meeting_svc = MeetingService()
                for meeting_data in result.get("meetings", []):
                    try:
                        meeting = await meeting_svc._create_meeting_from_data(
                            meeting_data, source_message,
                        )
                        if meeting:
                            meetings_created += 1
                    except Exception as e:
                        logger.error("Meeting creation failed | data=%s: %s", meeting_data, e, exc_info=True)

                logger.info("Batch processed | chat=%s topic=%s tasks=%d meetings=%d",
                            chat_id, topic_id, tasks_created, meetings_created)
        except Exception as e:
            logger.error("Batch extraction failed: %s", e, exc_info=True)

        await self._mark_messages_processed(msg_ids)
        return {"tasks_created": tasks_created, "meetings_created": meetings_created}

    async def _create_task(self, task_data: dict, source_message: Message):
        """Создаёт задачу и назначает исполнителей с детальным логгированием."""
        from core.services.task_service import TaskService
        
        # Используем TaskService для создания задачи с проверкой дубликатов
        task_service = TaskService()
        task = await task_service._create_task_from_data(task_data, source_message)
        
        if task:
            logger.info("Task created | id=%s title=%s", task.id, task.title)
        
        return task

    @sync_to_async
    def _load_messages(self, msg_ids: list[int]) -> list[Message]:
        return list(
            Message.objects.filter(id__in=msg_ids)
            .select_related("chat", "topic", "author")
            .order_by("timestamp")
        )

    @sync_to_async
    def _mark_messages_processed(self, msg_ids: list[int]) -> None:
        Message.objects.filter(id__in=msg_ids).update(is_processed=True)

    async def _store_embeddings(self, db_messages: list[Message]) -> None:
        texts = [m.text for m in db_messages]
        try:
            embeddings = await generate_embeddings_batch(texts)
        except Exception as e:
            logger.error("Batch embedding failed: %s", e, exc_info=True)
            return

        for msg, emb in zip(db_messages, embeddings):
            if emb is None:
                continue
            payload = {
                "message_id": msg.id,
                "chat_id": msg.chat.chat_id,
                "topic_id": msg.topic.thread_id if msg.topic else None,
                "user_id": msg.author.telegram_id,
                "username": msg.author.username,
                "timestamp": int(msg.timestamp.timestamp()),
                "text": msg.text[:1000],
            }
            try:
                await sync_to_async(self.vector_client.upsert_message)(msg.id, emb, payload)
            except Exception as e:
                logger.error("Qdrant upsert failed msg=%s: %s", msg.id, e)
