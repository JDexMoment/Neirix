from celery.schedules import crontab
from config.celery import app

app.conf.beat_schedule = {
    'process-messages-every-minute': {
        'task': 'celery_app.tasks.process_messages.process_new_messages_batch',
        'schedule': 60.0,
    },
    'send-meeting-reminders': {
        'task': 'celery_app.tasks.send_reminders.send_meeting_reminders',
        'schedule': 300.0,  # каждые 5 минут
    },
    'send-daily-task-reminders': {
        'task': 'celery_app.tasks.send_reminders.send_task_reminders',
        'schedule': crontab(hour=9, minute=0),  # 9:00 каждый день
    },
    'generate-daily-summaries': {
        'task': 'celery_app.tasks.generate_summary.generate_daily_summaries',
        'schedule': crontab(hour=8, minute=0),  # 8:00
    },
    'generate-weekly-summaries': {
        'task': 'celery_app.tasks.generate_summary.generate_weekly_summaries',
        'schedule': crontab(hour=18, minute=0, day_of_week=5),  # пятница 18:00
    },
}