import logging
import os
from datetime import datetime, timedelta
from typing import Optional, List
from fpdf import FPDF
import re

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.models import ComparisonSummary
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

    def _is_bot_username(self, username: str) -> bool:
        """Проверяет, является ли username ботом (заканчивается на _bot или Bot)."""
        if not username:
            return False
        return bool(re.search(r'[_]?[Bb]ot$', username))

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
            # Пропускаем сообщения от ботов
            author = msg.author
            if hasattr(author, 'is_bot') and author.is_bot:
                continue
            if author.username and self._is_bot_username(author.username):
                continue

            name = author.full_name or author.username or str(author.telegram_id)
            time_str = (
                timezone.localtime(msg.timestamp).strftime("%Y-%m-%d %H:%M")
                if timezone.is_aware(msg.timestamp)
                else msg.timestamp.strftime("%Y-%m-%d %H:%M")
            )
            lines.append(f"[{time_str}] {name}: {msg.text}")
        return "\n".join(lines)

    def _format_user_link(self, user) -> str:
        """
        Форматирует пользователя как ссылку.
        Боты пропускаются (возвращается None).
        """
        if hasattr(user, 'is_bot') and user.is_bot:
            return None
        if user.username and self._is_bot_username(user.username):
            return None

        if user.username:
            return f"@{user.username}"
        elif user.full_name:
            return f'<a href="tg://user?id={user.telegram_id}">{user.full_name}</a>'
        else:
            return f"id={user.telegram_id}"

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
                link = self._format_user_link(user)
                if link:  # None означает что это бот — пропускаем
                    assignee_names.append(link)

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
                link = self._format_user_link(p)
                if link:  # пропускаем ботов
                    participants.append(link)

            participants_str = ", ".join(participants) if participants else "Все участники"
            meeting_time = (
                timezone.localtime(meeting.start_at).strftime("%Y-%m-%d %H:%M")
                if timezone.is_aware(meeting.start_at)
                else meeting.start_at.strftime("%Y-%m-%d %H:%M")
            )
            lines.append(f"- {meeting.title} в {meeting_time} (участники: {participants_str})")

        return "\n".join(lines)

    async def generate_comparison_summary(
    self,
    topic: Topic,
    period1_start: datetime,
    period1_end: datetime,
    period2_start: datetime,
    period2_end: datetime
) -> Optional[ComparisonSummary]:
        # 1. Проверяем кэш
        cached = await sync_to_async(
            ComparisonSummary.objects.filter(
                topic=topic,
                period1_start=period1_start,
                period1_end=period1_end,
                period2_start=period2_start,
                period2_end=period2_end
            ).first
        )()
        if cached:
            return cached

        # 2. Получаем/генерируем обычные саммари за каждый период
        summary1 = await self.generate_summary_for_period(topic, period1_start, period1_end)
        summary2 = await self.generate_summary_for_period(topic, period2_start, period2_end)

        if not summary1 or not summary2:
            return None

        # 3. Вызываем LLM для сравнения
        try:
            comparison_text = await self.llm.generate_comparison_summary(
                summary1.content,
                summary2.content
            )
        except Exception:
            logger.exception("LLM comparison failed")
            return None

        # 4. Сохраняем результат
        comparison = await sync_to_async(ComparisonSummary.objects.create)(
            topic=topic,
            period1_start=period1_start,
            period1_end=period1_end,
            period2_start=period2_start,
            period2_end=period2_end,
            content=comparison_text
        )
        return comparison

    def generate_summary_pdf(self, summary) -> bytes:
        pdf = FPDF()
        pdf.add_page()

        # Пытаемся использовать Liberation Serif (аналог Times New Roman)
        font_regular = '/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf'
        font_bold = '/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf'
        if not os.path.exists(font_regular):
            # fallback на DejaVu Sans
            font_regular = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
            font_bold = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

        if os.path.exists(font_regular):
            pdf.add_font('Times', '', font_regular, uni=True)
            pdf.add_font('Times', 'B', font_bold, uni=True)
            font_family = 'Times'
        else:
            # Совсем без шрифта – кириллица не отобразится
            pdf.set_font('Helvetica', size=12)
            font_family = None

        # Заголовок
        start = summary.period_start.strftime('%d.%m.%Y') if summary.period_start else '?'
        end = summary.period_end.strftime('%d.%m.%Y') if summary.period_end else '?'
        if font_family:
            pdf.set_font(font_family, 'B', 14)
            pdf.cell(0, 10, f'Сводка за период {start} — {end}', ln=True, align='C')
            pdf.ln(10)
            pdf.set_font(font_family, '', 12)
        else:
            pdf.cell(0, 10, f'Summary {start} — {end}', ln=True, align='C')
            pdf.ln(10)

        # Тело саммари
        for line in summary.content.split('\n'):
            if font_family:
                pdf.set_font(font_family, '', 12)
                pdf.multi_cell(0, 10, line)
            else:
                pdf.set_font('Helvetica', '', 12)
                pdf.multi_cell(0, 10, line)

        return pdf.output()

   