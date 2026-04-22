import logging
from datetime import datetime, timedelta
from typing import List, Optional

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import Meeting, TelegramUser, Message
from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _find_user_by_username(clean_name: str) -> Optional[TelegramUser]:
    """
    Выделено в отдельную функцию чтобы избежать ловушки lambda в цикле.
    clean_name передаётся как аргумент — нет захвата по ссылке.
    """
    return TelegramUser.objects.filter(
        Q(username__iexact=clean_name) | Q(full_name__icontains=clean_name)
    ).first()


class MeetingService:
    def __init__(self):
        self.llm = LLMClient()

    async def extract_meeting_from_message(
        self,
        message: Message,
    ) -> Optional[Meeting]:
        """
        Извлекает встречу из сообщения через LLM и сохраняет в БД.
        """
        now = timezone.localtime(timezone.now())
        # Передаём в формате который совпадает с тем что ожидает промпт
        context_str = now.strftime("%Y-%m-%d %H:%M")

        meeting_data = await self.llm.extract_meeting_from_message(
            message.text,
            current_context=context_str,
        )
        if not meeting_data:
            return None

        # Дополнительная защита: title не должен быть пустым
        title = (meeting_data.get("title") or "").strip()
        if not title:
            logger.warning(
                "extract_meeting_from_message: empty title after LLM, skipping. "
                "message_id=%s text=%r",
                message.id, message.text,
            )
            return None

        try:
            # Парсим время и делаем aware (МСК или текущий TZ сервера)
            naive_dt = datetime.fromisoformat(meeting_data["start_at"])
            current_tz = timezone.get_current_timezone()
            start_at = timezone.make_aware(naive_dt, current_tz)

            meeting = await sync_to_async(Meeting.objects.create)(
                title=title,
                topic=message.topic,
                start_at=start_at,
                source_message=message,
            )
            logger.info(
                "Created meeting id=%s title=%r start_at=%s",
                meeting.id, title, start_at,
            )

            # Обработка участников
            participants_names: List[str] = meeting_data.get("participants", [])
            logger.debug(
                "Meeting id=%s: processing %d participants: %s",
                meeting.id, len(participants_names), participants_names,
            )

            for raw_name in participants_names:
                clean_name = raw_name.lstrip("@").strip()
                if not clean_name:
                    continue

                # Именованная функция вместо lambda — нет ловушки захвата по ссылке
                user: Optional[TelegramUser] = await sync_to_async(
                    _find_user_by_username
                )(clean_name)

                if user:
                    await sync_to_async(meeting.participants.add)(user)
                    logger.debug(
                        "Meeting id=%s: added participant %r (user_id=%s)",
                        meeting.id, raw_name, user.id,
                    )
                else:
                    logger.warning(
                        "Meeting id=%s: participant %r not found in DB, skipping",
                        meeting.id, raw_name,
                    )

            return meeting

        except KeyError as e:
            logger.error(
                "extract_meeting_from_message: missing field %s in meeting_data=%s",
                e, meeting_data,
            )
            return None
        except Exception as e:
            logger.error(
                "extract_meeting_from_message: unexpected error: %s",
                e, exc_info=True,
            )
            return None

    async def get_upcoming_meetings(self, hours_ahead: int = 24) -> List[Meeting]:
        """
        Исправление: обёрнуто в sync_to_async + возвращает list а не QuerySet.
        """
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
        """
        Исправление: обёрнуто в sync_to_async.
        """
        def _update() -> None:
            meeting.reminder_sent = True
            meeting.save(update_fields=["reminder_sent"])

        await sync_to_async(_update)()
        logger.info("Meeting id=%s: reminder marked as sent", meeting.id)