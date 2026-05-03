import logging
from datetime import datetime, timedelta
from typing import Optional, List

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.models import Topic, Message, Summary, Task, Meeting
from core.utils.llm_client import LLMClient
from vector_store.client import VectorStoreClient

logger = logging.getLogger(__name__)


class SummaryService:
    def __init__(self, llm: Optional[LLMClient] = None, vector_store: Optional[VectorStoreClient] = None):
        self._llm = llm
        self._vector_store = vector_store

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

    @property
    def vector_store(self) -> VectorStoreClient:
        if self._vector_store is None:
            self._vector_store = VectorStoreClient()
        return self._vector_store

    async def generate_summary_for_period(
        self,
        topic: Topic,
        period_start: datetime,
        period_end: datetime,
        include_similar_context: bool = True,
    ) -> Optional[Summary]:
        try:
            messages = await sync_to_async(self._get_messages_for_period)(topic, period_start, period_end)
            if not messages:
                logger.warning("No messages found for topic=%s", getattr(topic, "id", None))
                return None

            tasks = await sync_to_async(self._get_tasks_for_period)(topic, period_start, period_end)
            meetings = await sync_to_async(self._get_meetings_for_period)(topic, period_start, period_end)

            messages_context = self._format_messages_context(messages)
            tasks_context = self._format_tasks_context(tasks)
            meetings_context = self._format_meetings_context(meetings)

            if include_similar_context:
                similar_context = await self._get_similar_context(messages_context, topic)
                if similar_context:
                    messages_context += f"\n\nРелевантные предыдущие обсуждения:\n{similar_context}"

            content = await self.llm.generate_summary(
                messages_context=messages_context,
                tasks_context=tasks_context,
                meetings_context=meetings_context,
            )

            summary = await sync_to_async(Summary.objects.create)(
                topic=topic,
                period_start=period_start,
                period_end=period_end,
                content=content,
            )

            logger.info("Generated summary id=%s for topic=%s", summary.id, getattr(topic, "id", None))
            return summary

        except Exception:
            logger.exception("Failed to generate summary for topic=%s", getattr(topic, "id", None))
            raise

    def _get_messages_for_period(self, topic: Topic, period_start: datetime, period_end: datetime) -> List[Message]:
        return list(
            Message.objects.filter(
                topic=topic,
                timestamp__gte=period_start,
                timestamp__lt=period_end,
            )
            .select_related("author")
            .order_by("timestamp")
        )

    def _get_tasks_for_period(self, topic: Topic, period_start: datetime, period_end: datetime) -> List[Task]:
        return list(
            Task.objects.filter(
                topic=topic,
                created_at__gte=period_start,
                created_at__lt=period_end,
            )
            .prefetch_related("assignees__user")
            .order_by("created_at")
        )

    def _get_meetings_for_period(self, topic: Topic, period_start: datetime, period_end: datetime) -> List[Meeting]:
        return list(
            Meeting.objects.filter(
                topic=topic,
                start_at__gte=period_start,
                start_at__lt=period_end,
            )
            .prefetch_related("participants")
            .order_by("start_at")
        )

    def _format_messages_context(self, messages: List[Message]) -> str:
        lines = []
        for msg in messages:
            author = msg.author.full_name or msg.author.username or str(msg.author.telegram_id)
            time_str = timezone.localtime(msg.timestamp).strftime("%Y-%m-%d %H:%M") if timezone.is_aware(msg.timestamp) else msg.timestamp.strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{time_str}] {author}: {msg.text}")
        return "\n".join(lines)

    def _format_tasks_context(self, tasks: List[Task]) -> str:
        if not tasks:
            return ""

        lines = ["Поставленные задачи:"]
        for task in tasks:
            assignee_names = []
            for assignee_link in task.assignees.all():
                user = getattr(assignee_link, "user", None)
                if not user:
                    continue
                assignee_names.append(user.full_name or (f"@{user.username}" if user.username else str(user.telegram_id)))

            assignee_str = ", ".join(assignee_names) if assignee_names else "не назначен"
            due_str = task.due_date.strftime("%Y-%m-%d") if task.due_date else "без срока"

            if task.status == "done":
                status = "✅"
            elif task.status == "open":
                status = "🔄"
            else:
                status = "❌"

            lines.append(f"- {status} {task.title} (отв: {assignee_str}, до: {due_str})")

        return "\n".join(lines)

    def _format_meetings_context(self, meetings: List[Meeting]) -> str:
        if not meetings:
            return ""

        lines = ["Запланированные встречи:"]
        for meeting in meetings:
            participants = []
            for p in meeting.participants.all():
                participants.append(p.full_name or (f"@{p.username}" if p.username else str(p.telegram_id)))

            participants_str = ", ".join(participants) if participants else "Все участники"
            meeting_time = timezone.localtime(meeting.start_at).strftime("%Y-%m-%d %H:%M") if timezone.is_aware(meeting.start_at) else meeting.start_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"- {meeting.title} в {meeting_time} (участники: {participants_str})")

        return "\n".join(lines)

    async def _get_similar_context(self, query: str, topic: Topic, limit: int = 5) -> str:
        try:
            embedding = await self.llm.generate_embedding(query)

            results = await self.vector_store.search_similar(
                query_embedding=embedding,
                chat_id=topic.chat.chat_id,
                topic_id=topic.thread_id,
                limit=limit,
                time_range_days=30,
            )
            if not results:
                return ""

            message_ids = [r["payload"].get("message_id") for r in results if r.get("payload") and r["payload"].get("message_id")]
            if not message_ids:
                return ""

            messages = await sync_to_async(
                lambda: list(
                    Message.objects.filter(id__in=message_ids)
                    .select_related("author")
                    .order_by("timestamp")
                )
            )()

            return self._format_messages_context(messages)

        except Exception as e:
            logger.warning("Failed to get similar context: %s", e)
            return ""

    async def get_daily_summary(self, topic: Topic, date: Optional[datetime] = None) -> Optional[Summary]:
        if date is None:
            base_date = timezone.localdate()
        else:
            base_date = date.date() if isinstance(date, datetime) else date

        period_start = timezone.make_aware(datetime.combine(base_date, datetime.min.time()), timezone.get_current_timezone())
        period_end = period_start + timedelta(days=1)
        return await self.generate_summary_for_period(topic, period_start, period_end)

    async def get_weekly_summary(self, topic: Topic, week_start: Optional[datetime] = None) -> Optional[Summary]:
        if week_start is None:
            today = timezone.localdate()
            week_start_date = today - timedelta(days=today.weekday())
        else:
            week_start_date = week_start.date() if isinstance(week_start, datetime) else week_start

        period_start = timezone.make_aware(datetime.combine(week_start_date, datetime.min.time()), timezone.get_current_timezone())
        period_end = period_start + timedelta(days=7)
        return await self.generate_summary_for_period(topic, period_start, period_end)