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
        from datetime import datetime
        from django.utils import timezone
        from django.db.models import Q
        from core.models import Task

        title = (task_data.get("title") or "").strip()
        if not title:
            logger.warning("_create_task: empty title, skipping")
            return None

        due_date = None
        raw_due = task_data.get("due_date")
        if raw_due:
            try:
                naive_dt = datetime.strptime(str(raw_due)[:10], "%Y-%m-%d")
                naive_dt = naive_dt.replace(hour=23, minute=59, second=0, microsecond=0)
                current_tz = timezone.get_current_timezone()
                due_date = timezone.make_aware(naive_dt, current_tz)
            except (ValueError, TypeError) as e:
                logger.warning("_create_task: invalid due_date=%r: %s", raw_due, e)

        task = await sync_to_async(Task.objects.create)(
            title=title,
            description=(task_data.get("description") or "").strip(),
            topic=source_message.topic,
            due_date=due_date,
            source_message=source_message,
            status="open",
        )

        logger.info("_create_task: Task(id=%s, title=%r) created", task.id, task.title)

        assignee_names = task_data.get("assignees", [])
        logger.info("_create_task: processing %d assignees for task %s: %s",
                    len(assignee_names), task.id, assignee_names)

        for raw_name in assignee_names:
            clean_name = raw_name.lstrip("@").strip()
            if not clean_name:
                continue

            user = await sync_to_async(
                lambda n=clean_name: TelegramUser.objects.filter(
                    Q(username__iexact=n) | Q(full_name__icontains=n)
                ).first()
            )()

            if user:
                await sync_to_async(TaskAssignee.objects.create)(task=task, user=user)
                logger.info("_create_task: assignee %s (tg_id=%s) -> task %s",
                            clean_name, user.telegram_id, task.id)
            else:
                logger.error(
                    "_create_task: assignee %r NOT FOUND in TelegramUser! "
                    "Task %s has NO assignees -> WONT APPEAR in /tasks.", clean_name, task.id)

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
