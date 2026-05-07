import logging
from datetime import datetime, timedelta
from typing import List, Optional, TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import Meeting, TelegramUser, Message

if TYPE_CHECKING:
    from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _find_user_by_username(clean_name: str) -> Optional[TelegramUser]:
    return TelegramUser.objects.filter(
        Q(username__iexact=clean_name) | Q(full_name__icontains=clean_name)
    ).first()


class MeetingService:
    def __init__(self, llm: Optional["LLMClient"] = None):
        self._llm = llm

    @property
    def llm(self) -> "LLMClient":
        if self._llm is None:
            from core.utils.llm_client import LLMClient
            self._llm = LLMClient()
        return self._llm

    async def extract_meeting_from_message(self, message: Message) -> Optional[Meeting]:
        now = timezone.localtime(timezone.now())
        context_str = now.strftime("%Y-%m-%d %H:%M")

        meeting_data = await self.llm.extract_meeting_from_message(
            message.text,
            current_context=context_str,
        )
        if not meeting_data:
            return None

        title = (meeting_data.get("title") or "").strip()
        if not title:
            return None

        try:
            naive_dt = datetime.fromisoformat(meeting_data["start_at"])
            current_tz = timezone.get_current_timezone()
            start_at = timezone.make_aware(naive_dt, current_tz)

            meeting = await sync_to_async(Meeting.objects.create)(
                title=title,
                topic=message.topic,
                start_at=start_at,
                source_message=message,
            )

            participants_names: List[str] = meeting_data.get("participants", [])
            for raw_name in participants_names:
                clean_name = raw_name.lstrip("@").strip()
                if not clean_name:
                    continue

                user: Optional[TelegramUser] = await sync_to_async(_find_user_by_username)(clean_name)
                if user:
                    await sync_to_async(meeting.participants.add)(user)

            return meeting

        except Exception as e:
            logger.error("extract_meeting_from_message error: %s", e, exc_info=True)
            return None

    async def get_upcoming_meetings(self, hours_ahead: int = 24) -> List[Meeting]:
        def _query() -> List[Meeting]:
            now = timezone.now()
            end_time = now + timedelta(hours=hours_ahead)
            return list(
                Meeting.objects.filter(
                    start_at__gte=now,
                    start_at__lte=end_time,
                    reminder_sent=False,
                )
                .select_related("topic")
                .prefetch_related("participants")
            )
        return await sync_to_async(_query)()

    async def mark_reminder_sent(self, meeting: Meeting) -> None:
        def _update() -> None:
            meeting.reminder_sent = True
            meeting.save(update_fields=["reminder_sent"])
        await sync_to_async(_update)()

    async def cancel_meeting(self, meeting_id: int, bot=None) -> bool:
        def _cancel():
            try:
                meeting = Meeting.objects.get(id=meeting_id)
                meeting.status = "cancelled"
                meeting.save(update_fields=["status"])
                return True
            except Meeting.DoesNotExist:
                return False
        return await sync_to_async(_cancel)()

    async def reschedule_meeting(self, meeting_id: int, new_start_at: datetime):
        def _reschedule():
            try:
                meeting = Meeting.objects.get(id=meeting_id)
                meeting.start_at = new_start_at
                meeting.status = "active"
                meeting.reminder_sent = False
                meeting.daily_reminder_sent = False
                meeting.save(update_fields=["start_at", "status", "reminder_sent", "daily_reminder_sent"])
                return meeting
            except Meeting.DoesNotExist:
                return None
        return await sync_to_async(_reschedule)()

    async def get_meeting_by_id(self, meeting_id: int) -> Optional[Meeting]:
        def _get():
            return Meeting.objects.filter(id=meeting_id).select_related("topic__chat").prefetch_related("participants").first()
        return await sync_to_async(_get)()