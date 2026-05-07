from celery.schedules import crontab
from config.celery import app

app.conf.beat_schedule = {
    # ─── Обработка сообщений ─────────────────────────────────────────
    'process-messages-every-minute': {
        'task': 'celery_app.tasks.process_messages.process_new_messages_batch',
        'schedule': 60.0,
    },

    # ─── Напоминания о встречах ──────────────────────────────────────
    # За 1 час — каждые 5 минут
    'send-meeting-1h-reminders': {
        'task': 'celery_app.tasks.send_reminders.send_meeting_reminders',
        'schedule': crontab(minute='*/5'),
    },
    # За 24 часа — каждые 30 минут
    'send-meeting-24h-reminders': {
        'task': 'celery_app.tasks.send_reminders.send_meeting_24h_reminders',
        'schedule': crontab(minute='*/30'),
    },

    # ─── Напоминания о задачах ───────────────────────────────────────
    # За 24 часа до дедлайна — каждые 30 минут
    'send-task-24h-reminders': {
        'task': 'celery_app.tasks.send_reminders.send_task_24h_reminders',
        'schedule': crontab(minute='*/30'),
    },
    # Просроченные задачи — в 10:00 каждый день
    'send-overdue-task-reminders': {
        'task': 'celery_app.tasks.send_reminders.send_overdue_task_reminders',
        'schedule': crontab(hour=10, minute=0),
    },

    # ─── Утренний дайджест ───────────────────────────────────────────
    # Задачи и встречи на сегодня — в 9:00 каждый день
    'send-daily-digest': {
        'task': 'celery_app.tasks.send_reminders.send_daily_digest',
        'schedule': crontab(hour=9, minute=0),
    },

    # ─── Саммари ─────────────────────────────────────────────────────
    'generate-daily-summaries': {
        'task': 'celery_app.tasks.generate_summary.generate_daily_summaries',
        'schedule': crontab(hour=8, minute=0),
    },
    'generate-weekly-summaries': {
        'task': 'celery_app.tasks.generate_summary.generate_weekly_summaries',
        'schedule': crontab(hour=18, minute=0, day_of_week=5),
    },
}