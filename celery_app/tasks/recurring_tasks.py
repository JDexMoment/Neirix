"""
Celery-задачи для обслуживания повторяющихся задач и встреч.

Регистрация:
  - В celery_app/celery.py (или celery_app/tasks/__init__.py) добавить:
      from celery_app.tasks.recurring_tasks import *
  - Настроить периодический запуск через celery beat:
      CELERY_BEAT_SCHEDULE = {
          'check-recurring-every-hour': {
              'task': 'check_recurring_meetings',
              'schedule': crontab(minute=0),  # каждый час
          },
      }
"""
import logging

from celery import shared_task
from django.utils import timezone
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


@shared_task(name="check_recurring_meetings")
def check_recurring_meetings():
    """
    Проверяет все активные MeetingRecurrence.
    Если у какой-то серии осталось < 2 будущих инстансов — создаёт новые.
    Запускать раз в час через celery beat.
    """
    from core.models import MeetingRecurrence
    from core.services.recurrence_service import RecurrenceService

    svc = RecurrenceService()
    active_series = MeetingRecurrence.objects.filter(is_active=True)

    for rec in active_series:
        try:
            future_count = rec.instances.filter(
                start_at__gte=timezone.now(),
                status="active",
            ).count()
            if future_count < 2:
                async_to_sync(svc.ensure_meeting_instances)(rec.id, min_count=2)
                logger.info(
                    "Refilled meeting instances | rec_id=%s title=%s",
                    rec.id, rec.title,
                )
        except Exception as e:
            logger.error(
                "Failed to check recurring meeting | rec_id=%s: %s",
                rec.id, e, exc_info=True,
            )

    return f"Checked {active_series.count()} recurring meeting series"


@shared_task(name="check_recurring_tasks")
def check_recurring_tasks():
    """
    Проверяет все активные TaskRecurrence.
    Если next_occurrence уже прошёл, но задача не была создана
    (например, из-за ошибки при mark_task_done) — создаёт пропущенные.
    Запускать раз в день через celery beat.
    """
    from core.models import TaskRecurrence, Task
    from core.services.recurrence_service import RecurrenceService, get_next_occurrence

    svc = RecurrenceService()
    now = timezone.now()
    overdue = TaskRecurrence.objects.filter(
        is_active=True,
        next_occurrence__lte=now,
    )

    created_count = 0
    for rec in overdue:
        try:
            # Проверяем, не создали ли уже задачу на этот next_occurrence
            existing = Task.objects.filter(
                recurrence_group_id=rec.task.recurrence_group_id,
                status="open",
            ).exists()

            if not existing:
                # Создаём новую задачу
                next_occ = get_next_occurrence(
                    rec.cron_expression,
                    from_date=timezone.localtime(timezone.now()),
                )
                new_task = Task.objects.create(
                    title=rec.task.title,
                    description=rec.task.description,
                    topic=rec.task.topic,
                    due_date=next_occ,
                    source_message=rec.task.source_message,
                    creator=rec.task.creator,
                    status="open",
                    is_template=False,
                    recurrence_group_id=rec.task.recurrence_group_id,
                )
                # Копируем assignees
                for ta in rec.task.assignees.all():
                    from core.models import TaskAssignee
                    TaskAssignee.objects.create(task=new_task, user=ta.user)

                # Обновляем next_occurrence
                rec.next_occurrence = next_occ
                rec.save(update_fields=["next_occurrence"])
                created_count += 1
                logger.info(
                    "Recurring task auto-created | rec_id=%s task_id=%s",
                    rec.id, new_task.id,
                )
        except Exception as e:
            logger.error(
                "Failed to create recurring task | rec_id=%s: %s",
                rec.id, e, exc_info=True,
            )

    return f"Created {created_count} overdue recurring tasks"
