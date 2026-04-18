import logging
from datetime import datetime, timedelta
from typing import Optional
from django.utils import timezone
from asgiref.sync import sync_to_async
from django.utils import timezone as dj_timezone
from core.models import Meeting, Topic, TelegramUser, Message
from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)

class MeetingService:
    def __init__(self):
        self.llm = LLMClient()

    async def extract_meeting_from_message(self, message: Message) -> Optional[Meeting]:
        meeting_data = await self.llm.extract_meeting_from_message(message.text)
        if not meeting_data:
            return None

        try:
            start_at = dj_timezone.make_aware(datetime.fromisoformat(meeting_data['start_at']))
            # Создаём встречу через sync_to_async
            meeting = await sync_to_async(Meeting.objects.create)(
                title=meeting_data['title'],
                topic=message.topic,
                start_at=start_at,
                source_message=message
            )
            # Добавляем участников
            participants = meeting_data.get('participants', [])
            for name in participants:
                # Поиск пользователя тоже асинхронно
                user = await sync_to_async(
                    lambda: TelegramUser.objects.filter(full_name__icontains=name).first()
                )()
                if user:
                    await sync_to_async(meeting.participants.add)(user)
            logger.info(f"Created meeting {meeting.id} from message {message.id}")
            return meeting
        except Exception as e:
            logger.error(f"Failed to create meeting from extracted data: {e}")
            return None

    def get_upcoming_meetings(self, hours_ahead: int = 24) -> list:
        now = timezone.now()
        end_time = now + timedelta(hours=hours_ahead)
        return Meeting.objects.filter(
            start_at__gte=now,
            start_at__lte=end_time,
            reminder_sent=False
        ).select_related('topic').prefetch_related('participants')

    def mark_reminder_sent(self, meeting: Meeting) -> None:
        meeting.reminder_sent = True
        meeting.save()  