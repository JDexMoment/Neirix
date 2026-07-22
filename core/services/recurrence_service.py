"""
Сервис для повторяющихся задач и встреч (Recurring Tasks & Meetings).
"""
import logging
import uuid
import re
from datetime import datetime, timedelta, time
from typing import Optional

from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import (
    Task, TaskRecurrence, Meeting, MeetingRecurrence,
    MeetingRecurrenceParticipant, TelegramUser, Topic, Message,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# Парсинг recurrence из текста
# ────────────────────────────────────────────────────────────

_CRON_WEEKDAYS = {
    "monday": 1, "понедельник": 1, "понедельника": 1, "пн": 1,
    "tuesday": 2, "вторник": 2, "вторника": 2, "вт": 2,
    "wednesday": 3, "среда": 3, "среду": 3, "ср": 3,
    "thursday": 4, "четверг": 4, "четверга": 4, "чт": 4,
    "friday": 5, "пятница": 5, "пятницу": 5, "пятницы": 5, "пт": 5,
    "saturday": 6, "суббота": 6, "субботу": 6, "сб": 6,
    "sunday": 7, "воскресенье": 7, "воскресенья": 7, "вс": 7,
}


def parse_recurrence(text: str) -> Optional[dict]:
    if not text:
        return None

    text_lower = text.lower().strip()
    human = text

    hour, minute = 9, 0
    time_match = re.search(r'(\d{1,2}):(\d{2})', text_lower)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))

    days_of_week = []
    day_of_month = None

    if re.search(r'кажд[ыу][йю] день|ежедневно|every day|daily', text_lower):
        period = "daily"
    elif re.search(r'кажд[ыу][йю]\s+\d+-?[еы][еи]?\s+числ[ао]', text_lower):
        period = "monthly"
        num_match = re.search(r'(\d+)', text_lower)
        if num_match:
            day_of_month = int(num_match.group(1))
    elif re.search(r'кажд[ыу][йю]|every|каждую|each', text_lower):
        period = "weekly"
        for word in text_lower.split():
            if word in _CRON_WEEKDAYS:
                days_of_week.append(_CRON_WEEKDAYS[word])
        if not days_of_week:
            for key, val in _CRON_WEEKDAYS.items():
                if key in text_lower:
                    days_of_week.append(val)
        if not days_of_week:
            period = "weekly"
    else:
        return None

    if period == "daily":
        cron = f"{minute} {hour} * * *"
        human_readable = f"каждый день в {hour:02d}:{minute:02d}"
    elif period == "weekly":
        if days_of_week:
            cron_days = ",".join(str(d) for d in sorted(set(days_of_week)))
            cron = f"{minute} {hour} * * {cron_days}"
            # Берём одно каноническое имя для каждого дня
            day_names_raw = [k for k, v in _CRON_WEEKDAYS.items()
                           if v in set(days_of_week) and not k.isascii()]
            seen_nums = set()
            day_names = []
            for k in day_names_raw:
                v = _CRON_WEEKDAYS[k]
                if v not in seen_nums:
                    seen_nums.add(v)
                    day_names.append(k)
            day_str = ", ".join(day_names) if day_names else f"день(дни) {cron_days}"
            human_readable = f"каждый {day_str} в {hour:02d}:{minute:02d}"
        else:
            cron = f"{minute} {hour} * * *"
            human_readable = f"еженедельно в {hour:02d}:{minute:02d}"
    elif period == "monthly":
        day = day_of_month or 1
        cron = f"{minute} {hour} {day} * *"
        human_readable = f"каждое {day}-е число в {hour:02d}:{minute:02d}"
    else:
        return None

    return {
        "cron": cron,
        "human": human_readable or human,
        "period": period,
        "days_of_week": list(set(days_of_week)),
        "day_of_month": day_of_month,
        "hour": hour,
        "minute": minute,
    }


def get_next_occurrence(cron_expr: str, from_date: Optional[datetime] = None) -> datetime:
    if from_date is None:
        from_date = timezone.localtime(timezone.now())

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        logger.warning("Invalid cron expression: %s", cron_expr)
        return from_date + timedelta(days=1)

    minute = int(parts[0])
    hour = int(parts[1])
    day_of_month = parts[2]
    day_of_week = parts[4]

    candidate = from_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if candidate <= from_date:
        candidate += timedelta(days=1)

    if day_of_month == "*" and day_of_week == "*":
        return candidate

    if day_of_week != "*":
        target_days = {int(d) for d in day_of_week.split(",")}
        max_iterations = 14
        iterations = 0
        while candidate.isoweekday() not in target_days and iterations < max_iterations:
            candidate += timedelta(days=1)
            iterations += 1
        return candidate

    if day_of_month != "*":
        target_day = int(day_of_month)
        max_iterations = 31
        iterations = 0
        while candidate.day != target_day and iterations < max_iterations:
            candidate += timedelta(days=1)
            iterations += 1
        return candidate

    return candidate


# ────────────────────────────────────────────────────────────
# RecurrenceService
# ────────────────────────────────────────────────────────────

class RecurrenceService:

    # ── Задачи ──────────────────────────────────────────────

    async def create_recurring_task(
        self,
        task: Task,
        cron_expression: str,
        human_readable: str,
    ) -> TaskRecurrence:
        next_occ = get_next_occurrence(cron_expression, task.due_date or timezone.now())

        def _create():
            task.is_template = True
            task.recurrence_group_id = uuid.uuid4()
            task.save(update_fields=["is_template", "recurrence_group_id"])

            return TaskRecurrence.objects.create(
                task=task,
                cron_expression=cron_expression,
                human_readable=human_readable,
                is_active=True,
                next_occurrence=next_occ,
            )

        return await sync_to_async(_create)()

    async def on_task_completed(self, task: Task) -> Optional[Task]:
        def _get_recurrence():
            if not task.recurrence_group_id:
                return None
            try:
                return TaskRecurrence.objects.select_related("task").get(
                    task__recurrence_group_id=task.recurrence_group_id,
                    is_active=True,
                )
            except TaskRecurrence.DoesNotExist:
                return None

        recurrence = await sync_to_async(_get_recurrence)()
        if not recurrence:
            return None

        # Берём оригинальное название из template-задачи
        original_title = recurrence.task.title if recurrence.task else task.title
        # Начинаем со следующего дня после due_date (начало дня, а не 23:59)
        due = task.due_date or timezone.now()
        local_due = timezone.localtime(due)
        next_day_start = local_due.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        next_occ = get_next_occurrence(
            recurrence.cron_expression,
            from_date=next_day_start,
        )

        def _create_next():
            new_task = Task.objects.create(
                title=original_title,
                description=task.description,
                topic=task.topic,
                due_date=next_occ,
                source_message=task.source_message,
                creator=task.creator,
                status="open",
                is_template=False,
                recurrence_group_id=task.recurrence_group_id,
            )
            for ta in task.assignees.select_related("user").all():
                from core.models import TaskAssignee
                TaskAssignee.objects.create(task=new_task, user=ta.user)

            recurrence.next_occurrence = next_occ
            recurrence.save(update_fields=["next_occurrence"])

            return new_task

        return await sync_to_async(_create_next)()

    async def cancel_task_series(self, task: Task) -> bool:
        def _cancel():
            try:
                recurrence = TaskRecurrence.objects.get(
                    task__recurrence_group_id=task.recurrence_group_id,
                    is_active=True,
                )
            except TaskRecurrence.DoesNotExist:
                return False

            recurrence.is_active = False
            recurrence.save(update_fields=["is_active"])

            # Отменяем ВСЕ открытые задачи в группе
            Task.objects.filter(
                recurrence_group_id=task.recurrence_group_id,
                status="open",
            ).update(status="cancelled")

            return True

        return await sync_to_async(_cancel)()

    async def get_task_series_tasks(self, task: Task) -> list:
        def _get():
            return list(
                Task.objects.filter(
                    recurrence_group_id=task.recurrence_group_id,
                ).order_by("-due_date")
            )
        return await sync_to_async(_get)()

    # ── Встречи ─────────────────────────────────────────────

    async def create_recurring_meeting(
        self,
        title: str,
        topic: Topic,
        creator: TelegramUser,
        cron_expression: str,
        human_readable: str,
        participants: list[TelegramUser],
        source_message: Optional[Message],
        is_all_hands: bool = False,
        instance_count: int = 2,
        existing_meeting: Optional[Meeting] = None,
    ) -> MeetingRecurrence:
        def _create():
            rec = MeetingRecurrence.objects.create(
                title=title,
                topic=topic,
                creator=creator,
                cron_expression=cron_expression,
                human_readable=human_readable,
                duration_minutes=60,
                is_all_hands=is_all_hands,
                is_active=True,
                source_message=source_message,
            )

            if participants:
                for user in participants:
                    MeetingRecurrenceParticipant.objects.create(
                        recurring_meeting=rec, user=user,
                    )

            if existing_meeting:
                existing_meeting.recurrence = rec
                existing_meeting.save(update_fields=["recurrence"])

            from_date = existing_meeting.start_at if existing_meeting else timezone.now()
            next_date = get_next_occurrence(cron_expression, from_date + timedelta(hours=1))
            original_hour = from_date.hour if timezone.is_aware(from_date) else from_date.hour
            original_minute = from_date.minute if timezone.is_aware(from_date) else from_date.minute

            for _ in range(instance_count):
                adjusted_date = next_date.replace(hour=original_hour, minute=original_minute, second=0, microsecond=0)
                if timezone.is_aware(next_date) and timezone.is_naive(adjusted_date):
                    adjusted_date = timezone.make_aware(adjusted_date, timezone.get_current_timezone())
                meeting = Meeting.objects.create(
                    title=title,
                    topic=topic,
                    start_at=adjusted_date,
                    source_message=source_message,
                    creator=creator,
                    status="active",
                    is_all_hands=is_all_hands,
                    recurrence=rec,
                )
                if existing_meeting and not is_all_hands:
                    for user in participants:
                        meeting.participants.add(user)
                next_date = get_next_occurrence(cron_expression, next_date + timedelta(hours=1))

            return rec

        return await sync_to_async(_create)()

    async def create_next_meeting_instance(self, recurrence: MeetingRecurrence) -> Meeting:
        def _create():
            last_instance = (
                Meeting.objects.filter(recurrence=recurrence)
                .order_by("-start_at")
                .first()
            )
            from_date = last_instance.start_at if last_instance else timezone.now()
            next_date = get_next_occurrence(
                recurrence.cron_expression,
                from_date=from_date + timedelta(hours=1),
            )

            participants_qs = recurrence.participant_links.all()
            participant_users = [p.user for p in participants_qs]

            meeting = Meeting.objects.create(
                title=recurrence.title,
                topic=recurrence.topic,
                start_at=next_date,
                source_message=recurrence.source_message,
                creator=recurrence.creator,
                status="active",
                is_all_hands=recurrence.is_all_hands,
                recurrence=recurrence,
            )
            if not recurrence.is_all_hands:
                for user in participant_users:
                    meeting.participants.add(user)

            return meeting

        return await sync_to_async(_create)()

    async def ensure_meeting_instances(self, recurrence_id: int, min_count: int = 2):
        def _count():
            try:
                rec = MeetingRecurrence.objects.get(id=recurrence_id, is_active=True)
            except MeetingRecurrence.DoesNotExist:
                return None, 0

            future_count = Meeting.objects.filter(
                recurrence=rec,
                start_at__gte=timezone.now(),
                status="active",
            ).count()
            return rec, future_count

        rec, count = await sync_to_async(_count)()
        if rec is None:
            return

        while count < min_count:
            meeting = await self.create_next_meeting_instance(rec)
            logger.info(
                "Created next meeting instance | rec_id=%s meeting_id=%s title=%s",
                rec.id, meeting.id, meeting.title,
            )
            count += 1

    async def cancel_meeting_series(self, recurrence_id: int) -> bool:
        def _cancel():
            try:
                rec = MeetingRecurrence.objects.get(id=recurrence_id, is_active=True)
            except MeetingRecurrence.DoesNotExist:
                return False

            rec.is_active = False
            rec.save(update_fields=["is_active"])

            Meeting.objects.filter(
                recurrence=rec,
                status="active",
            ).update(status="cancelled")

            return True

        return await sync_to_async(_cancel)()

    async def cancel_single_meeting(self, meeting: Meeting) -> bool:
        def _cancel():
            meeting.status = "cancelled"
            meeting.save(update_fields=["status"])
            return True

        return await sync_to_async(_cancel)()

    async def update_series_title(self, recurrence, new_title: str) -> bool:
        def _update():
            recurrence.title = new_title
            recurrence.save(update_fields=["title"])

            Meeting.objects.filter(
                recurrence=recurrence,
                start_at__gte=timezone.now(),
                status="active",
            ).update(title=new_title)

            Task.objects.filter(
                recurrence_group_id=recurrence.recurrence_group_id,
                status="open",
            ).update(title=new_title)

            return True
        return await sync_to_async(_update)()

    async def update_series_participants(self, recurrence, usernames: list[str]) -> bool:
        from core.services.meeting_service import _find_user_by_username

        users = []
        for raw_name in usernames:
            clean_name = raw_name.lstrip("@").strip()
            if not clean_name:
                continue
            user = await sync_to_async(_find_user_by_username)(clean_name)
            if user:
                users.append(user)

        def _update():
            recurrence.participant_links.all().delete()
            for user in users:
                MeetingRecurrenceParticipant.objects.create(
                    recurring_meeting=recurrence, user=user,
                )

            future_meetings = Meeting.objects.filter(
                recurrence=recurrence,
                start_at__gte=timezone.now(),
                status="active",
            )
            for meeting in future_meetings:
                meeting.participants.clear()
                if not recurrence.is_all_hands:
                    for user in users:
                        meeting.participants.add(user)

            return True

        return await sync_to_async(_update)()
