import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from core.models import Topic
from core.services.summary_service import SummaryService

logger = logging.getLogger(__name__)


@shared_task
def generate_daily_summaries():
    """Генерирует дневные саммари для всех активных тем"""
    today = timezone.now().date()
    yesterday = today - timedelta(days=1)
    topics = Topic.objects.filter(is_active=True).select_related('chat')

    service = SummaryService()
    for topic in topics:
        try:
            summary = service.get_daily_summary(topic, yesterday)
            if summary:
                logger.info(f"Generated daily summary for topic {topic.id}")
        except Exception as e:
            logger.error(f"Failed daily summary for topic {topic.id}: {e}")


@shared_task
def generate_weekly_summaries():
    """Генерирует недельные саммари для всех активных тем (за прошлую неделю)"""
    topics = Topic.objects.filter(is_active=True).select_related('chat')
    service = SummaryService()
    for topic in topics:
        try:
            # Определяем начало прошлой недели
            today = timezone.now().date()
            start_of_this_week = today - timedelta(days=today.weekday())
            start_of_last_week = start_of_this_week - timedelta(days=7)
            summary = service.get_weekly_summary(topic, start_of_last_week)
            if summary:
                logger.info(f"Generated weekly summary for topic {topic.id}")
        except Exception as e:
            logger.error(f"Failed weekly summary for topic {topic.id}: {e}")