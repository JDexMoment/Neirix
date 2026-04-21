import logging
from celery import shared_task
from core.services.meeting_service import MeetingService
from core.services.task_service import TaskService
from core.models import TelegramUser

# Импорт функции отправки уведомлений (будет позже в bot/services)
# from bot.services.notification_sender import send_reminder

logger = logging.getLogger(__name__)


@shared_task
def send_meeting_reminders():
    """Отправляет напоминания о предстоящих встречах"""
    meeting_service = MeetingService()
    upcoming = meeting_service.get_upcoming_meetings(hours_ahead=1)  # за час до встречи

    for meeting in upcoming:
        # Здесь будет отправка уведомлений участникам
        # for participant in meeting.participants.all():
        #     send_reminder(participant, f"Напоминание: встреча '{meeting.title}' начнется в {meeting.start_at}")
        meeting_service.mark_reminder_sent(meeting)
        logger.info(f"Reminder sent for meeting {meeting.id}")
    return len(upcoming)


@shared_task
def send_task_reminders():
    """Отправляет напоминания о просроченных задачах и задачах на сегодня"""
    task_service = TaskService()
    overdue = task_service.get_overdue_tasks()
    # Логика отправки уведомлений
    logger.info(f"Found {overdue.count()} overdue tasks")