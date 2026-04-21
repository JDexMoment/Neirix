import logging
from datetime import datetime, timedelta
from typing import Optional
from django.utils import timezone
from django.db.models import Q
from asgiref.sync import sync_to_async
from django.utils import timezone as dj_timezone
from core.models import Meeting, Topic, TelegramUser, Message
from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)

class MeetingService:
    def __init__(self):
        self.llm = LLMClient()

    async def extract_meeting_from_message(self, message: Message) -> Optional[Meeting]:
        # Получаем текущее локальное время сервера
        now = dj_timezone.localtime(dj_timezone.now())
        context_str = now.strftime("%Y-%m-%d %H:%M (%A)") # Пример: 2026-04-20 14:00 (Monday)
        
        meeting_data = await self.llm.extract_meeting_from_message(message.text, context_str)
        if not meeting_data:
            return None

        try:
            # Парсим время от LLM и делаем его "aware" (с осознанием часового пояса МСК)
            naive_dt = datetime.fromisoformat(meeting_data['start_at'])
            current_tz = dj_timezone.get_current_timezone()
            start_at = dj_timezone.make_aware(naive_dt, current_tz)

            meeting = await sync_to_async(Meeting.objects.create)(
                title=meeting_data['title'],
                topic=message.topic,
                start_at=start_at,
                source_message=message
            )

            # Обработка участников
            participants_names = meeting_data.get('participants', [])
            for name in participants_names:
                clean_name = name.replace('@', '').strip()
                # Ищем по username (без @) или по полному имени
                user = await sync_to_async(
                    lambda: TelegramUser.objects.filter(
                        Q(username__iexact=clean_name) | Q(full_name__icontains=clean_name)
                    ).first()
                )()
                if user:
                    await sync_to_async(meeting.participants.add)(user)
            
            logger.info(f"Created meeting {meeting.id} for {start_at}")
            return meeting
        except Exception as e:
            logger.error(f"Error creating meeting: {e}")
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