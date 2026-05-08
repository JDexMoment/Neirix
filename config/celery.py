import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

from celery_app.schedule import BEAT_SCHEDULE

app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    **BEAT_SCHEDULE,
}