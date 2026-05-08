from celery.schedules import crontab

BEAT_SCHEDULE = {
    # ─── Напоминания о встречах ──────────────────────────────────────
    "send-meeting-1h-reminders": {
        "task": "celery_app.tasks.send_reminders.send_meeting_reminders",
        "schedule": crontab(minute="*/5"),
    },
    "send-meeting-24h-reminders": {
        "task": "celery_app.tasks.send_reminders.send_meeting_24h_reminders",
        "schedule": crontab(minute="*/30"),
    },

    # ─── Напоминания о задачах ───────────────────────────────────────
    "send-task-24h-reminders": {
        "task": "celery_app.tasks.send_reminders.send_task_24h_reminders",
        "schedule": crontab(minute="*/30"),
    },
    "send-overdue-task-reminders": {
        "task": "celery_app.tasks.send_reminders.send_overdue_task_reminders",
        "schedule": crontab(hour=10, minute=0),
    },

    # ─── Утренний дайджест ───────────────────────────────────────────
    "send-daily-digest": {
        "task": "celery_app.tasks.send_reminders.send_daily_digest",
        "schedule": crontab(hour=9, minute=0),
    },

    # ─── Саммари ─────────────────────────────────────────────────────
    "generate-daily-summaries": {
        "task": "celery_app.tasks.generate_summary.generate_daily_summaries",
        "schedule": crontab(hour=8, minute=0),
    },
    "generate-weekly-summaries": {
        "task": "celery_app.tasks.generate_summary.generate_weekly_summaries",
        "schedule": crontab(hour=18, minute=0, day_of_week=5),
    },
}