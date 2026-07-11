import logging

from asgiref.sync import sync_to_async

from core.models import Message, TaskAssignee, TelegramUser, UserRole
from vector_store.client import VectorStoreClient
from vector_store.embeddings import generate_embeddings_batch
from core.services.permissions import user_can_create

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

        # ════════════════════════════════════════════════════════════
        # Фильтр: оставляем только сообщения от manager/admin
        # Сообщения от member не отправляем в LLM
        # ════════════════════════════════════════════════════════════
        authorized_messages = []
        for msg in db_messages:
            try:
                if await sync_to_async(user_can_create)(msg):
                    authorized_messages.append(msg)
            except Exception:
                # Если проверка прав недоступна (тесты, миграции) — пропускаем
                authorized_messages.append(msg)
        skipped = len(db_messages) - len(authorized_messages)
        if skipped:
            logger.info(
                "Skipped %d messages from members (not sent to LLM)",
                skipped,
            )

        if not authorized_messages:
            logger.info("No authorized messages in batch, skipping LLM call")
            await self._mark_messages_processed(msg_ids)
            return {"tasks_created": 0, "meetings_created": 0}

        await self._store_embeddings(db_messages)  # всё равно индексируем всё

        tasks_created = 0
        meetings_created = 0
        unassigned_task_ids: list[int] = []
        unassigned_meeting_ids: list[int] = []
        source_message = authorized_messages[-1]

        try:
            from core.utils.llm_client import LLMClient
            llm = LLMClient()
            result = await llm.extract_all_from_messages(authorized_messages)

            if result:
                for task_data in result.get("tasks", []):
                    try:
                        task = await self._create_task(task_data, source_message)
                        if task:
                            tasks_created += 1
                            logger.info("Task created | id=%s title=%s", task.id, task.title)
                            # Проверяем, нужен ли исполнитель
                            assignee_count = await sync_to_async(
                                lambda: task.assignees.count()
                            )()
                            if assignee_count == 0:
                                unassigned_task_ids.append(task.id)
                    except Exception as e:
                        logger.error("Task creation failed | data=%s: %s", task_data, e, exc_info=True)

                from core.services.meeting_service import MeetingService
                meeting_svc = MeetingService()
                for meeting_data in result.get("meetings", []):
                    try:
                        # ═══ Проверяем ОРИГИНАЛЬНЫЕ данные LLM ═══
                        # Если participants — пустой список [] → уведомляем
                        # Если participants = ["Все участники"] → НЕ уведомляем
                        llm_participants = meeting_data.get("participants", [])
                        truly_empty = (
                            isinstance(llm_participants, list)
                            and len(llm_participants) == 0
                        )

                        meeting = await meeting_svc._create_meeting_from_data(
                            meeting_data, source_message,
                        )
                        if meeting:
                            meetings_created += 1
                            if truly_empty:
                                unassigned_meeting_ids.append(meeting.id)
                    except Exception as e:
                        logger.error("Meeting creation failed | data=%s: %s", meeting_data, e, exc_info=True)

                logger.info("Batch processed | chat=%s topic=%s tasks=%d meetings=%d",
                            chat_id, topic_id, tasks_created, meetings_created)
        except Exception as e:
            logger.error("Batch extraction failed: %s", e, exc_info=True)

        await self._mark_messages_processed(msg_ids)
        return {
            "tasks_created": tasks_created,
            "meetings_created": meetings_created,
            "unassigned_task_ids": unassigned_task_ids,
            "unassigned_meeting_ids": unassigned_meeting_ids,
        }

    async def _create_task(self, task_data: dict, source_message: Message):
        """Создаёт задачу с проверкой дубликатов через TaskService."""
        from core.services.task_service import TaskService

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
