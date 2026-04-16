import logging
from datetime import datetime, timedelta
from typing import Optional
from django.utils import timezone
from core.models import Meeting, Topic, TelegramUser, Message
from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


class MeetingService:
    def __init__(self):
        self.llm = LLMClient()

    async def extract_meeting_from_message(self, message: Message) -> Optional[Meeting]:
        """Извлекает информацию о встрече из сообщения и сохраняет в БД"""
        meeting_data = await self.llm.extract_meeting_from_message(message.text)
        if not meeting_data:
            return None

        try:
            start_at = datetime.fromisoformat(meeting_data['start_at'])
            meeting = Meeting.objects.create(
                title=meeting_data['title'],
                topic=message.topic,
                start_at=start_at,
                source_message=message
            )
            # Добавляем участников (поиск по именам)
            participants = meeting_data.get('participants', [])
            for name in participants:
                user = TelegramUser.objects.filter(full_name__icontains=name).first()
                if user:
                    meeting.participants.add(user)
            logger.info(f"Created meeting {meeting.id} from message {message.id}")
            return meeting
        except Exception as e:
            logger.error(f"Failed to create meeting from extracted data: {e}")
            return None

    def get_upcoming_meetings(self, hours_ahead: int = 24) -> list:
        """Возвращает встречи в ближайшие N часов"""
        now = timezone.now()
        end_time = now + timedelta(hours=hours_ahead)
        return Meeting.objects.filter(
            start_at__gte=now,
            start_at__lte=end_time,
            reminder_sent=False
        ).select_related('topic').prefetch_related('participants')

    def mark_reminder_sent(self, meeting: Meeting) -> None:
        """Отмечает, что напоминание отправлено"""
        meeting.reminder_sent = True
        meeting.save()