import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import Meeting, TelegramUser, Message

if TYPE_CHECKING:
    from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _is_bot_user(user: TelegramUser) -> bool:
    if hasattr(user, "is_bot") and user.is_bot:
        return True
    if user.username and re.search(r"[_]?[Bb]ot$", user.username):
        return True
    return False


def _find_user_by_username(clean_name: str) -> Optional[TelegramUser]:
    return TelegramUser.objects.filter(
        Q(username__iexact=clean_name) | Q(full_name__icontains=clean_name)
    ).first()


def _parse_start_at_from_meeting_data(meeting_data: dict) -> Optional[datetime]:
    current_tz = timezone.get_current_timezone()

    raw_start_at = (meeting_data.get("start_at") or "").strip()
    if raw_start_at:
        try:
            parsed = datetime.fromisoformat(raw_start_at)
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, current_tz)
            return timezone.localtime(parsed, current_tz)
        except ValueError:
            logger.warning("Invalid start_at format: %r", raw_start_at)

    raw_date = (meeting_data.get("date") or "").strip()
    raw_time = (meeting_data.get("time") or "").strip()

    if not raw_date or not raw_time:
        logger.warning(
            "Meeting skipped: incomplete date/time | date=%r time=%r",
            raw_date, raw_time,
        )
        return None

    try:
        naive_dt = datetime.strptime(f"{raw_date} {raw_time}", "%Y-%m-%d %H:%M")
        return timezone.make_aware(naive_dt, current_tz)
    except ValueError:
        logger.warning("Invalid meeting date/time | date=%r time=%r", raw_date, raw_time)
        return None


class MeetingService:
    def __init__(self, llm: Optional["LLMClient"] = None):
        self._llm = llm

    @property
    def llm(self) -> "LLMClient":
        if self._llm is None:
            from core.utils.llm_client import LLMClient
            self._llm = LLMClient()
        return self._llm

    # ─────────────────────────────────────────────────────────────────
    #  Создание встреч
    # ─────────────────────────────────────────────────────────────────

    async def _create_meeting_from_data(
        self, meeting_data: dict, source_message: Message,
    ) -> Optional[Meeting]:
        try:
            title = (meeting_data.get("title") or "").strip()
            if not title:
                logger.warning("_create_meeting_from_data: empty title, skipping")
                return None

            start_at = _parse_start_at_from_meeting_data(meeting_data)
            if not start_at:
                return None

            meeting = await sync_to_async(Meeting.objects.create)(
                title=title,
                topic=source_message.topic,
                start_at=start_at,
                source_message=source_message,
            )

            participants_names: List[str] = meeting_data.get("participants", [])
            for raw_name in participants_names:
                clean_name = raw_name.lstrip("@").strip()
                if not clean_name:
                    continue

                user: Optional[TelegramUser] = await sync_to_async(
                    _find_user_by_username
                )(clean_name)

                if user:
                    await sync_to_async(meeting.participants.add)(user)
                else:
                    logger.warning(
                        "Meeting id=%s: participant %r not found", meeting.id, raw_name,
                    )

            return meeting

        except Exception as e:
            logger.error("_create_meeting_from_data error: %s", e, exc_info=True)
            return None

    async def extract_meeting_from_message(self, message: Message) -> Optional[Meeting]:
        now = timezone.localtime(timezone.now())
        meeting_data = await self.llm.extract_meeting_from_message(
            message.text, current_context=now.strftime("%Y-%m-%d %H:%M"),
        )
        if not meeting_data:
            return None
        return await self._create_meeting_from_data(meeting_data, message)

    async def extract_meetings_from_messages_batch(
        self, messages: list["Message"],
    ) -> list[Meeting]:
        if not messages:
            return []

        messages = sorted(messages, key=lambda m: m.timestamp)

        context_lines = []
        for msg in messages:
            author = (
                f"@{msg.author.username}"
                if msg.author and msg.author.username
                else (msg.author.full_name or str(msg.author.telegram_id))
            )
            time_str = timezone.localtime(msg.timestamp).strftime("%H:%M")
            context_lines.append(f"[{time_str}] {author}: {msg.text}")

        batch_text = "\n".join(context_lines)
        now = timezone.localtime(timezone.now())

        try:
            meetings_data = await self.llm.extract_meetings_from_messages(
                batch_text, current_context=now.strftime("%Y-%m-%d %H:%M"),
            )
        except Exception as e:
            logger.error("Batch meeting extraction failed: %s", e, exc_info=True)
            return []

        if not meetings_data:
            return []

        created: list[Meeting] = []
        source_message = messages[-1]
        for meeting_data in meetings_data:
            meeting = await self._create_meeting_from_data(meeting_data, source_message)
            if meeting:
                created.append(meeting)

        return created

    # ─────────────────────────────────────────────────────────────────
    #  Запросы
    # ─────────────────────────────────────────────────────────────────

    async def get_upcoming_meetings(self, hours_ahead: int = 24) -> List[Meeting]:
        def _query() -> List[Meeting]:
            now = timezone.now()
            return list(
                Meeting.objects.filter(
                    start_at__gte=now,
                    start_at__lte=now + timedelta(hours=hours_ahead),
                    reminder_sent=False,
                )
                .select_related("topic")
                .prefetch_related("participants")
            )
        return await sync_to_async(_query)()

    async def get_meeting_by_id(self, meeting_id: int) -> Optional[Meeting]:
        def _get():
            return (
                Meeting.objects.filter(id=meeting_id)
                .select_related("topic__chat")
                .prefetch_related("participants")
                .first()
            )
        return await sync_to_async(_get)()

    async def mark_reminder_sent(self, meeting: Meeting) -> None:
        def _update():
            meeting.reminder_sent = True
            meeting.save(update_fields=["reminder_sent"])
        await sync_to_async(_update)()

    # ─────────────────────────────────────────────────────────────────
    #  Отмена встречи  (с уведомлением всех участников)
    # ─────────────────────────────────────────────────────────────────

    async def cancel_meeting(
        self,
        meeting_id: int,
        notification_sender=None,  # NotificationSender | None
    ) -> bool:
        """
        Отменяет встречу. Если передан notification_sender —
        уведомляет всех участников (кроме ботов) в их ветки.
        """
        meeting = await self.get_meeting_by_id(meeting_id)
        if not meeting:
            return False

        def _cancel():
            meeting.status = "cancelled"
            meeting.save(update_fields=["status"])

        await sync_to_async(_cancel)()

        # Уведомляем участников
        if notification_sender is not None:
            participants = list(meeting.participants.all())
            for user in participants:
                if _is_bot_user(user):
                    continue
                await notification_sender.send_meeting_cancelled(user, meeting)

        return True

    # ─────────────────────────────────────────────────────────────────
    #  Перенос встречи  (с уведомлением всех участников)
    # ─────────────────────────────────────────────────────────────────

    async def reschedule_meeting(
        self,
        meeting_id: int,
        new_start_at: datetime,
        notification_sender=None,  # NotificationSender | None
    ) -> Optional[Meeting]:
        """
        Переносит встречу. Если передан notification_sender —
        уведомляет всех участников о переносе.
        Возвращает обновлённую встречу или None.
        """
        old_meeting = await self.get_meeting_by_id(meeting_id)
        if not old_meeting:
            return None

        old_start_at = old_meeting.start_at

        def _reschedule():
            old_meeting.start_at = new_start_at
            old_meeting.status = "active"
            old_meeting.reminder_sent = False
            old_meeting.daily_reminder_sent = False
            old_meeting.save(
                update_fields=["start_at", "status", "reminder_sent", "daily_reminder_sent"]
            )
            return old_meeting

        meeting = await sync_to_async(_reschedule)()

        # Уведомляем участников
        if notification_sender is not None:
            participants = list(meeting.participants.all())
            for user in participants:
                if _is_bot_user(user):
                    continue
                await notification_sender.send_meeting_rescheduled(
                    user, meeting, old_start_at,
                )

        return meeting
