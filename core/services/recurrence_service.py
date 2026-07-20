"""
Сервис для повторяющихся задач и встреч (Recurring Tasks & Meetings).

Зависит от:
  - core/models.py (TaskRecurrence, MeetingRecurrence, MeetingRecurrenceParticipant)
  - Дополнительные поля: Meeting.recurrence, Task.is_template, Task.recurrence_group_id
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
    "monday": 1, "понедельник": 1, "пн": 1,
    "tuesday": 2, "вторник": 2, "вт": 2,
    "wednesday": 3, "среда": 3, "ср": 3,
    "thursday": 4, "четверг": 4, "чт": 4,
    "friday": 5, "пятница": 5, "пт": 5,
    "saturday": 6, "суббота": 6, "сб": 6,
    "sunday": 7, "воскресенье": 7, "вс": 7,
}


def parse_recurrence(text: str) -> Optional[dict]:
    """
    Парсит человекочитаемое recurrence.
    Примеры:
      "каждый понедельник в 10:00"
      "каждую пятницу"
      "каждый день в 9:00"
      "каждое 1-е число в 12:00"
      "каждый вторник и четверг в 11:00"

    Возвращает:
      {
        "cron": "0 10 * * 1",
        "human": "каждый понедельник в 10:00",
        "period": "weekly",
        "days_of_week": [1],
        "hour": 10,
        "minute": 0,
      }
      или None, если не распознано.
    """
    if not text:
        return None

    text_lower = text.lower().strip()
    human = text  # сохраняем оригинал

    # Извлекаем время HH:MM
    hour, minute = 9, 0  # default 9:00
    time_match = re.search(r'(\d{1,2}):(\d{2})', text_lower)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))

    # Определяем период
    days_of_week = []
    day_of_month = None

    # Ежедневно
    if re.search(r'кажд[ыу][йю] день|ежедневно|every day|daily', text_lower):
        period = "daily"
    # Ежемесячно по числу
    elif re.search(r'кажд[ыу][йю]\s+\d+-?[еы][еи]?\s+числ[ао]', text_lower):
        period = "monthly"
        num_match = re.search(r'(\d+)', text_lower)
        if num_match:
            day_of_month = int(num_match.group(1))
    # Еженедельно по дням
    elif re.search(r'кажд[ыу][йю]|every|каждую|each', text_lower):
        period = "weekly"
        # Ищем дни недели
        for word in text_lower.split():
            if word in _CRON_WEEKDAYS:
                days_of_week.append(_CRON_WEEKDAYS[word])
        # Если не нашли явных дней, берём "сегодня + 1 день" ? Нет, лучше день недели исходного сообщения
        if not days_of_week:
            # Возможно "каждую неделю" без дня — ставим текущий день недели
            # (будет переопределён при создании)
            period = "weekly"
    else:
        return None  # Не распознано

    # Строим cron
    if period == "daily":
        cron = f"{minute} {hour} * * *"
        human_readable = f"каждый день в {hour:02d}:{minute:02d}"
    elif period == "weekly":
        if days_of_week:
            cron_days = ",".join(str(d) for d in sorted(days_of_week))
            cron = f"{minute} {hour} * * {cron_days}"
            day_names = [k for k, v in _CRON_WEEKDAYS.items() if v in days_of_week and not k.isascii()]
            day_str = ", ".join(set(day_names)) if day_names else f"день(дни) {cron_days}"
            human_readable = f"каждый {day_str} в {hour:02d}:{minute:02d}"
        else:
            # Без указания дня — поставим заглушку, день будет определён при создании
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
        "days_of_week": days_of_week,
        "day_of_month": day_of_month,
        "hour": hour,
        "minute": minute,
    }


def get_next_occurrence(cron_expr: str, from_date: Optional[datetime] = None) -> datetime:
    """
    Вычисляет следующую дату/время по упрощённому cron.
    Поддерживает: "M H * * *", "M H * * D", "M H D * *"
    """
    if from_date is None:
        from_date = timezone.localtime(timezone.now())

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        logger.warning("Invalid cron expression: %s", cron_expr)
        return from_date + timedelta(days=1)

    minute = int(parts[0])
    hour = int(parts[1])
    day_of_month = parts[2]  # "*" or number
    day_of_week = parts[4]   # "*" or number(s)

    candidate = from_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Если время уже прошло сегодня — начинаем с завтра
    if candidate <= from_date:
        candidate += timedelta(days=1)

    # Ежедневно
    if day_of_month == "*" and day_of_week == "*":
        return candidate

    # Еженедельно по дням
    if day_of_week != "*":
        target_days = {int(d) for d in day_of_week.split(",")}
        max_iterations = 14  # защита от бесконечного цикла
        iterations = 0
        while candidate.isoweekday() not in target_days and iterations < max_iterations:
            candidate += timedelta(days=1)
            iterations += 1
        return candidate

    # Ежемесячно
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
    """Сервис для управления повторяющимися задачами и встречами."""

    # ── Задачи ──────────────────────────────────────────────

    async def create_recurring_task(
        self,
        task: Task,
        cron_expression: str,
        human_readable: str,
    ) -> TaskRecurrence:
        """
        Создаёт TaskRecurrence для существующей задачи.
        Задача помечается as is_template=True.
        """
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
        """
        Вызывается после mark_task_done.
        Если задача — template с активным recurrence, создаёт новую.
        """
        def _get_recurrence():
            try:
                return TaskRecurrence.objects.select_related("task").get(
                    task=task, is_active=True
                )
            except TaskRecurrence.DoesNotExist:
                return None

        recurrence = await sync_to_async(_get_recurrence)()
        if not recurrence:
            return None

        # Вычисляем следующую дату
        next_occ = get_next_occurrence(
            recurrence.cron_expression,
            from_date=timezone.localtime(timezone.now()),
        )

        # Создаём следующую задачу
        def _create_next():
            new_task = Task.objects.create(
                title=task.title,
                description=task.description,
                topic=task.topic,
                due_date=next_occ,
                source_message=task.source_message,
                creator=task.creator,
                status="open",
                is_template=False,
                recurrence_group_id=task.recurrence_group_id,
            )
            # Копируем assignees
            for ta in task.assignees.all():
                from core.models import TaskAssignee
                TaskAssignee.objects.create(task=new_task, user=ta.user)

            # Обновляем next_occurrence в рекурренсе
            recurrence.next_occurrence = next_occ
            recurrence.save(update_fields=["next_occurrence"])

            return new_task

        return await sync_to_async(_create_next)()

    async def cancel_task_series(self, task: Task) -> bool:
        """
        Отменяет всю серию повторяющихся задач:
        - Деактивирует TaskRecurrence
        - Отменяет все будущие задачи в группе
        """
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

            # Отменяем все будущие открытые задачи в группе (кроме текущей)
            Task.objects.filter(
                recurrence_group_id=task.recurrence_group_id,
                status="open",
            ).exclude(id=task.id).update(status="cancelled")

            return True

        return await sync_to_async(_cancel)()

    async def get_task_series_tasks(self, task: Task) -> list:
        """Возвращает все задачи в серии."""
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
    ) -> MeetingRecurrence:
        """
        Создаёт MeetingRecurrence + N будущих инстансов Meeting.
        """
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

            # Создаём N будущих инстансов
            next_date = get_next_occurrence(cron_expression)
            for _ in range(instance_count):
                Meeting.objects.create(
                    title=title,
                    topic=topic,
                    start_at=next_date,
                    source_message=source_message,
                    creator=creator,
                    status="active",
                    is_all_hands=is_all_hands,
                    recurrence=rec,
                )
                next_date = get_next_occurrence(cron_expression, next_date + timedelta(hours=1))

            return rec

        return await sync_to_async(_create)()

    async def create_next_meeting_instance(self, recurrence: MeetingRecurrence) -> Meeting:
        """Создаёт один следующий инстанс встречи."""
        def _create():
            # Находим последний созданный инстанс
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
        """
        Проверяет, что у рекурренса есть хотя бы min_count будущих инстансов.
        Если нет — создаёт недостающие.
        """
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
        """
        Отменяет всю серию встреч:
        - MeetingRecurrence.is_active = False
        - Все будущие инстансы → cancelled
        """
        def _cancel():
            try:
                rec = MeetingRecurrence.objects.get(id=recurrence_id, is_active=True)
            except MeetingRecurrence.DoesNotExist:
                return False

            rec.is_active = False
            rec.save(update_fields=["is_active"])

            Meeting.objects.filter(
                recurrence=rec,
                start_at__gte=timezone.now(),
                status="active",
            ).update(status="cancelled")

            return True

        return await sync_to_async(_cancel)()

    async def cancel_single_meeting(self, meeting: Meeting) -> bool:
        """
        Отменяет только одну встречу (серия продолжается).
        После отмены проверяет, нужно ли создать новый инстанс взамен.
        """
        def _cancel():
            meeting.status = "cancelled"
            meeting.save(update_fields=["status"])
            return True

        result = await sync_to_async(_cancel)()
        if result and meeting.recurrence_id:
            # Проверяем, не упало ли количество будущих ниже минимума
            await self.ensure_meeting_instances(meeting.recurrence_id, min_count=2)

        return result

    async def update_series_title(self, recurrence, new_title: str) -> bool:
        """Обновляет название во всех будущих инстансах и в самом рекурренсе."""
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
        """Обновляет участников во всех будущих инстансах."""
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
            # Обновляем список участников рекурренса
            recurrence.participant_links.all().delete()
            for user in users:
                MeetingRecurrenceParticipant.objects.create(
                    recurring_meeting=recurrence, user=user,
                )

            # Обновляем во всех будущих встречах
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
