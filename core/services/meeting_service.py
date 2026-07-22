import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import Meeting, TelegramUser, Message, Topic
from core.services.permissions import user_can_create
from core.services.recurrence_service import RecurrenceService, parse_recurrence


if TYPE_CHECKING:
    from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


_USERNAME_RE = re.compile(r"@\w+")
_CLEAN_EDGES_RE = re.compile(r"^[\s,\-|]+|[\s,\-|]+$")


def _find_user_by_username(clean_name: str) -> Optional[TelegramUser]:
    return TelegramUser.objects.filter(
        Q(username__iexact=clean_name) | Q(full_name__icontains=clean_name)
    ).first()


def _is_bot_user(user: TelegramUser) -> bool:
    if hasattr(user, "is_bot") and user.is_bot:
        return True
    if user.username and re.search(r"[_]?[Bb]ot$", user.username):
        return True
    return False


def _clean_title(title: str) -> str:
    cleaned = _USERNAME_RE.sub("", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = _CLEAN_EDGES_RE.sub("", cleaned).strip()
    return cleaned


def _is_author_mentioned(source_message: Message) -> bool:
    if not source_message.author or not source_message.author.username:
        return False
    mention = f"@{source_message.author.username}".lower()
    return mention in (source_message.text or "").lower()


def _filter_author_from_list(names: List[str], source_message: Message) -> List[str]:
    if _is_author_mentioned(source_message):
        return names
    if not source_message.author or not source_message.author.username:
        return names
    author_mention = f"@{source_message.author.username}".lower()
    return [n for n in names if n.lower() != author_mention]


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
        logger.warning("Meeting skipped: incomplete date/time | date=%r time=%r", raw_date, raw_time)
        return None
    try:
        naive_dt = datetime.strptime(f"{raw_date} {raw_time}", "%Y-%m-%d %H:%M")
        return timezone.make_aware(naive_dt, current_tz)
    except ValueError:
        logger.warning("Invalid meeting date/time | date=%r time=%r", raw_date, raw_time)
        return None


@sync_to_async
def _resolve_topic_for_private_message(source_message: Message):
    chat = source_message.chat
    if chat.type != "private":
        return source_message.topic

    author = source_message.author
    if not author:
        return source_message.topic

    from core.models import UserRole
    linked_role = (
        UserRole.objects.filter(user=author)
        .select_related("chat")
        .first()
    )
    if not linked_role:
        logger.warning(
            "_resolve_topic: user %s has no linked chat, falling back to source topic",
            author,
        )
        return source_message.topic

    linked_chat = linked_role.chat
    linked_topic, _ = Topic.objects.get_or_create(
        chat=linked_chat,
        thread_id=0,
        defaults={"is_active": True},
    )
    return linked_topic


class MeetingService:
    def __init__(self, llm: Optional["LLMClient"] = None):
        self._llm = llm

    @property
    def llm(self) -> "LLMClient":
        if self._llm is None:
            from core.utils.llm_client import LLMClient
            self._llm = LLMClient()
        return self._llm

    async def _create_meeting_from_data(
        self, meeting_data: dict, source_message: Message,
    ) -> Optional[Meeting]:
        try:
            title = (meeting_data.get("title") or "").strip()
            if not title:
                logger.warning("empty title, skipping")
                return None

            clean_title = _clean_title(title)
            if not clean_title:
                logger.warning("title empty after cleaning")
                return None

            topic = await _resolve_topic_for_private_message(source_message)
            if not await sync_to_async(user_can_create)(source_message):
                logger.warning("Permission denied for meeting creation")
                return None

            start_at = _parse_start_at_from_meeting_data(meeting_data)
            if not start_at:
                return None

            # ── Участники ──────────────────────────────────────────
            participants_names: List[str] = meeting_data.get("participants", [])

            # ════════════════════════════════════════════════════════════
            # БАТЧ-ФИКС: если source_message НЕ содержит упоминания
            # ни об одном из participants, значит встреча была извлечена
            # из ДРУГОГО сообщения — не фильтруем автора.
            # ════════════════════════════════════════════════════════════
            author_mentioned_in_batch = _is_author_mentioned_in_batch(
                participants_names, source_message
            )

            if author_mentioned_in_batch:
                participants_names = _filter_author_from_list(participants_names, source_message)

            has_all = False
            participant_objects: List[TelegramUser] = []

            for raw_name in participants_names:
                clean_name = raw_name.lstrip("@").strip()
                if not clean_name:
                    continue

                if clean_name.lower() in ("все участники", "все", "всем", "all"):
                    has_all = True
                    continue

                user: Optional[TelegramUser] = await sync_to_async(_find_user_by_username)(clean_name)
                if user and not _is_bot_user(user):
                    participant_objects.append(user)
                elif not user:
                    logger.warning("participant %r not found, creating placeholder", raw_name)
                    try:
                        placeholder = await sync_to_async(TelegramUser.objects.create)(
                            telegram_id=-(abs(hash(clean_name)) % 1_000_000_000 + 1_000_000_000),
                            username=clean_name,
                            full_name=clean_name,
                            is_bot=False,
                        )
                        participant_objects.append(placeholder)
                    except Exception:
                        logger.warning("Failed to create placeholder for %s", clean_name)

            # Проверка дубликата
            existing = await sync_to_async(self._check_duplicate_meeting)(
                clean_title, start_at, topic, participant_objects,
            )
            if existing:
                    # Обновляем is_all_hands при дубликате
                if has_all and not existing.is_all_hands:
                    def _patch():
                        Meeting.objects.filter(id=existing.id).update(is_all_hands=True)
                        existing.is_all_hands = True
                    await sync_to_async(_patch)()
                logger.info("Duplicate, skipping")
                return existing

            meeting = await sync_to_async(Meeting.objects.create)(
                title=clean_title,
                topic=topic,
                start_at=start_at,
                source_message=source_message,
                creator=source_message.author,
                is_all_hands=has_all,
            )

            if not has_all:
                for user in participant_objects:
                    await sync_to_async(meeting.participants.add)(user)

                    # ════════════════════════════════════════════════════════════
            # Повторяющиеся встречи: если LLM вернула recurrence
            # ════════════════════════════════════════════════════════════
            recurrence_raw = meeting_data.get("recurrence")
            if recurrence_raw:
                parsed = parse_recurrence(str(recurrence_raw))
                if parsed:
                    rec_svc = RecurrenceService()
                    await rec_svc.create_recurring_meeting(
                        title=clean_title,
                        topic=topic,
                        creator=source_message.author,
                        cron_expression=parsed["cron"],
                        human_readable=parsed["human"],
                        participants=participant_objects,
                        source_message=source_message,
                        is_all_hands=has_all,
                        instance_count=2,
                        existing_meeting=meeting,  
                    )
                    logger.info(
                        "Recurring meeting created | title=%s cron=%s human=%s",
                        clean_title, parsed["cron"], parsed["human"],
                    )

            return meeting

        except Exception as e:
            logger.error("_create_meeting_from_data error: %s", e, exc_info=True)
            return None

    def _check_duplicate_meeting(
        self, title: str, start_at: datetime, topic, participants: List[TelegramUser],
    ) -> Optional[Meeting]:
        possible = Meeting.objects.filter(
            title=title, start_at=start_at, topic=topic, status="active",
        )
        new_ids = {p.id for p in participants}
        for meeting in possible:
            existing_ids = set(meeting.participants.values_list("id", flat=True))
            if existing_ids == new_ids:
                return meeting
        return None

    async def extract_meeting_from_message(self, message: Message) -> Optional[Meeting]:
        now = timezone.localtime(timezone.now())
        meeting_data = await self.llm.extract_meeting_from_message(
            message.text, current_context=now.strftime("%Y-%m-%d %H:%M"),
        )
        return await self._create_meeting_from_data(meeting_data, message) if meeting_data else None

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

    async def update_participants(
        self, meeting_id: int, participant_usernames: List[str],
    ) -> bool:
        def _update() -> bool:
            try:
                meeting = Meeting.objects.get(id=meeting_id)
                meeting.participants.clear()
                meeting.is_all_hands = False
                for raw_name in participant_usernames:
                    clean_name = raw_name.lstrip("@").strip()
                    if not clean_name:
                        continue
                    if clean_name.lower() in ("все участники", "все", "всем", "all", "all participants"):
                        meeting.is_all_hands = True
                        continue
                    user = _find_user_by_username(clean_name)
                    if user and not _is_bot_user(user):
                        meeting.participants.add(user)
                meeting.save(update_fields=["is_all_hands"])
                return True
            except Meeting.DoesNotExist:
                return False
        return await sync_to_async(_update)()

    async def update_title(self, meeting_id: int, new_title: str) -> bool:
        """Обновляет название встречи."""
        def _update() -> bool:
            try:
                meeting = Meeting.objects.get(id=meeting_id)
                clean = _clean_title(new_title)
                if not clean:
                    return False
                meeting.title = clean
                meeting.save(update_fields=["title"])
                return True
            except Meeting.DoesNotExist:
                return False
        return await sync_to_async(_update)()

    async def cancel_meeting(self, meeting_id: int, notification_sender=None) -> bool:
        meeting = await self.get_meeting_by_id(meeting_id)
        if not meeting:
            return False
        def _cancel():
            meeting.status = "cancelled"
            meeting.save(update_fields=["status"])
        await sync_to_async(_cancel)()
        if notification_sender is not None:
            for user in meeting.participants.all():
                if _is_bot_user(user):
                    continue
                await notification_sender.send_meeting_cancelled(user, meeting)
        return True

    async def reschedule_meeting(self, meeting_id: int, new_start_at: datetime, notification_sender=None) -> Optional[Meeting]:
        old_meeting = await self.get_meeting_by_id(meeting_id)
        if not old_meeting:
            return None
        old_start_at = old_meeting.start_at
        def _reschedule():
            old_meeting.start_at = new_start_at
            old_meeting.status = "active"
            old_meeting.reminder_sent = False
            old_meeting.daily_reminder_sent = False
            old_meeting.save(update_fields=["start_at", "status", "reminder_sent", "daily_reminder_sent"])
            return old_meeting
        meeting = await sync_to_async(_reschedule)()
        if notification_sender is not None:
            for user in meeting.participants.all():
                if _is_bot_user(user):
                    continue
                await notification_sender.send_meeting_rescheduled(user, meeting, old_start_at)
        return meeting


# ══════════════════════════════════════════════════════════════════
# Вспомогательная функция для батч-фикса
# ══════════════════════════════════════════════════════════════════


def _is_author_mentioned_in_batch(participants: List[str], source_message: Message) -> bool:
    """
    Возвращает True, если хотя бы один участник упомянут
    в тексте source_message. Это значит, что встреча была
    извлечена ИЗ ЭТОГО сообщения → можно применять фильтр автора.
    Если ни один участник не упомянут → встреча из другого сообщения
    в батче → НЕ фильтруем автора.
    """
    if not source_message or not source_message.text:
        return True  # fallback — фильтруем как раньше

    text_lower = source_message.text.lower()
    for raw_name in participants:
        clean_name = raw_name.lstrip("@").strip().lower()
        if clean_name and clean_name in text_lower:
            return True
    return False
