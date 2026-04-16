import logging
from datetime import datetime, timedelta
from typing import Optional, List
from django.db.models import Q
from django.utils import timezone
from core.models import Topic, Message, Summary, Task, Meeting
from core.utils.llm_client import LLMClient
from vector_store.client import VectorStoreClient
from vector_store.embeddings import generate_embedding

logger = logging.getLogger(__name__)


class SummaryService:
    def __init__(self):
        self.llm = LLMClient()
        self.vector_store = VectorStoreClient()

    async def generate_summary_for_period(
            self,
            topic: Topic,
            period_start: datetime,
            period_end: datetime,
            include_similar_context: bool = True
    ) -> Optional[Summary]:
        """Генерирует саммари для темы за указанный период"""
        try:
            # Получаем сообщения из БД
            messages = Message.objects.filter(
                topic=topic,
                timestamp__gte=period_start,
                timestamp__lte=period_end
            ).select_related('author').order_by('timestamp')

            if not messages.exists():
                logger.warning(f"No messages found for topic {topic.id} in period")
                return None

            # Формируем контекст сообщений
            messages_context = self._format_messages_context(messages)

            # Получаем задачи за период
            tasks = Task.objects.filter(
                topic=topic,
                created_at__gte=period_start,
                created_at__lte=period_end
            ).select_related('assignee')
            tasks_context = self._format_tasks_context(tasks)

            # Получаем встречи за период
            meetings = Meeting.objects.filter(
                topic=topic,
                start_at__gte=period_start,
                start_at__lte=period_end
            )
            meetings_context = self._format_meetings_context(meetings)

            # Дополнительный контекст из векторного поиска (если нужно)
            if include_similar_context:
                similar_context = await self._get_similar_context(messages_context, topic)
                if similar_context:
                    messages_context += f"\n\nРелевантные предыдущие обсуждения:\n{similar_context}"

            # Генерируем саммари через LLM
            content = await self.llm.generate_summary(
                messages_context=messages_context,
                tasks_context=tasks_context,
                meetings_context=meetings_context
            )

            # Сохраняем в БД
            summary = Summary.objects.create(
                topic=topic,
                period_start=period_start,
                period_end=period_end,
                content=content
            )
            logger.info(f"Generated summary {summary.id} for topic {topic.id}")
            return summary

        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            raise

    def _format_messages_context(self, messages) -> str:
        """Форматирует сообщения в текст для LLM"""
        lines = []
        for msg in messages:
            author = msg.author.full_name or msg.author.username or str(msg.author.telegram_id)
            time_str = msg.timestamp.strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{time_str}] {author}: {msg.text}")
        return "\n".join(lines)

    def _format_tasks_context(self, tasks) -> str:
        """Форматирует задачи в текст"""
        if not tasks:
            return ""
        lines = ["Поставленные задачи:"]
        for task in tasks:
            assignee = task.assignee.full_name if task.assignee else "не назначен"
            status = "✅" if task.status == "done" else "🔄" if task.status == "open" else "❌"
            lines.append(f"- {status} {task.title} (отв: {assignee}, до: {task.due_date})")
        return "\n".join(lines)

    def _format_meetings_context(self, meetings) -> str:
        """Форматирует встречи в текст"""
        if not meetings:
            return ""
        lines = ["Запланированные встречи:"]
        for meeting in meetings:
            participants = ", ".join([p.full_name for p in meeting.participants.all()])
            lines.append(f"- {meeting.title} в {meeting.start_at} (участники: {participants})")
        return "\n".join(lines)

    async def _get_similar_context(self, query: str, topic: Topic, limit: int = 5) -> str:
        """Получает похожие сообщения из векторного хранилища"""
        try:
            embedding = await generate_embedding(query)
            results = await self.vector_store.search_similar(
                query_embedding=embedding,
                chat_id=topic.chat.chat_id,
                topic_id=topic.thread_id,
                limit=limit,
                time_range_days=30
            )
            if not results:
                return ""

            # Получаем ID сообщений из payload
            message_ids = [r['payload'].get('message_id') for r in results if r.get('payload')]
            if not message_ids:
                return ""

            messages = Message.objects.filter(id__in=message_ids).select_related('author')
            return self._format_messages_context(messages)
        except Exception as e:
            logger.warning(f"Failed to get similar context: {e}")
            return ""

    async def get_daily_summary(self, topic: Topic, date: Optional[datetime] = None) -> Optional[Summary]:
        """Саммари за день"""
        if date is None:
            date = timezone.now().date()
        period_start = datetime.combine(date, datetime.min.time())
        period_end = period_start + timedelta(days=1)
        return await self.generate_summary_for_period(topic, period_start, period_end)

    async def get_weekly_summary(self, topic: Topic, week_start: Optional[datetime] = None) -> Optional[Summary]:
        """Саммари за неделю (7 дней)"""
        if week_start is None:
            today = timezone.now().date()
            week_start = today - timedelta(days=today.weekday())  # понедельник
        period_start = datetime.combine(week_start, datetime.min.time())
        period_end = period_start + timedelta(days=7)
        return await self.generate_summary_for_period(topic, period_start, period_end)