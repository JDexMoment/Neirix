import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import Task, TelegramUser, Message, TaskAssignee, Topic
from core.services.permissions import user_can_create
from core.services.recurrence_service import RecurrenceService, parse_recurrence


if TYPE_CHECKING:
    from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


_USERNAME_RE = re.compile(r"@\w+")
_CLEAN_EDGES_RE = re.compile(r"^[\s,\-|]+|[\s,\-|]+$")


def _find_user_by_username(clean_name: str) -> Optional[TelegramUser]:
    return TelegramUser.objects.filter(
        Q(username__iexact=clean_name) | Q(full_name__icontains=clean_name)
    ).first()


def _is_bot_user(user: TelegramUser) -> bool:
    if hasattr(user, "is_bot") and user.is_bot:
        return True
    if user.username and re.search(r"[_]?[Bb]ot$", user.username):
        return True
    return False


def _clean_title(title: str) -> str:
    cleaned = _USERNAME_RE.sub("", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = _CLEAN_EDGES_RE.sub("", cleaned).strip()
    return cleaned


def _clean_project_parent(parent: str) -> str:
    """Чистит родительский заголовок проекта.

    Из '[15.08.2026 14:56] Евгений: задача для @X и @Y на август это
    сделать проект' делает 'сделать проект'.
    """
    p = parent
    p = re.sub(r"\[[^\]]*\]", "", p)          # [14:56] / [15.08...]
    p = p.split(":")[-1]                        # убираем 'Автор:' в начале
    p = _USERNAME_RE.sub("", p)                # @username
    p = re.sub(r"(?i)\bзадача\s+для\b", "", p)
    p = re.sub(r"(?i)\bэто\b", "", p)
    p = re.sub(
        r"(?i)\bна\s+(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма\w*|июн\w*|"
        r"июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\b",
        "", p,
    )
    p = re.sub(r"\s+\bи\b\s+", " ", p)         # одинокий союз 'и'
    p = re.sub(r"\s+", " ", p).strip(" \t-–—,:")
    return p


def _is_author_mentioned(source_message: Message) -> bool:
    if not source_message.author or not source_message.author.username:
        return False
    mention = f"@{source_message.author.username}".lower()
    return mention in (source_message.text or "").lower()


def _filter_author_from_list(names: List[str], source_message: Message) -> List[str]:
    if _is_author_mentioned(source_message):
        return names
    if not source_message.author or not source_message.author.username:
        return names
    author_mention = f"@{source_message.author.username}".lower()
    return [n for n in names if n.lower() != author_mention]


@sync_to_async
def _resolve_topic_for_private_message(source_message: Message) -> Optional[Topic]:
    chat = source_message.chat
    if chat.type != "private":
        return source_message.topic

    author = source_message.author
    if not author:
        return source_message.topic

    from core.models import UserRole
    linked_role = (
        UserRole.objects.filter(user=author)
        .select_related("chat")
        .first()
    )
    if not linked_role:
        logger.warning(
            "_resolve_topic: user %s has no linked chat, falling back to source topic",
            author,
        )
        return source_message.topic

    linked_chat = linked_role.chat
    linked_topic, _ = Topic.objects.get_or_create(
        chat=linked_chat,
        thread_id=0,
        defaults={"is_active": True},
    )
    return linked_topic


class TaskService:
    def __init__(self, llm: Optional["LLMClient"] = None):
        self._llm = llm

    @property
    def llm(self) -> "LLMClient":
        if self._llm is None:
            from core.utils.llm_client import LLMClient
            self._llm = LLMClient()
        return self._llm

    # ── Создание задачи ─────────────────────────────────────────

    async def _create_task_from_data(
        self, task_data: dict, source_message: Message,
    ) -> Optional[Task]:
        try:
            title = (task_data.get("title") or "").strip()
            if not title:
                logger.warning("_create_task_from_data: empty title, skipping")
                return None

            clean_title = _clean_title(title)
            if not clean_title:
                logger.warning("_create_task_from_data: title empty after cleaning")
                return None

            topic = await _resolve_topic_for_private_message(source_message)

            if not await sync_to_async(user_can_create)(source_message):
                logger.warning(
                    "Permission denied: user %s cannot create tasks in chat %s",
                    source_message.author, source_message.chat,
                )
                return None

            due_date = None
            raw_due = task_data.get("due_date")
            if raw_due:
                try:
                    naive_dt = datetime.strptime(str(raw_due)[:10], "%Y-%m-%d")
                    naive_dt = naive_dt.replace(hour=23, minute=59, second=0, microsecond=0)
                    current_tz = timezone.get_current_timezone()
                    due_date = timezone.make_aware(naive_dt, current_tz)
                except (ValueError, TypeError):
                    logger.warning("_create_task_from_data: invalid due_date=%r", raw_due)

            assignees: List[str] = task_data.get("assignees", [])
            logger.info("DEBUG batch_assignees before filter: %s (source_author=%s, source_text=%r)",
                       assignees,
                       source_message.author.username if source_message and source_message.author else "None",
                       source_message.text[:100] if source_message and source_message.text else "None")

            if _is_author_mentioned_in_batch(assignees, source_message):
                assignees = _filter_author_from_list(assignees, source_message)
                logger.info("DEBUG batch_assignees filter APPLIED -> %s", assignees)
            else:
                logger.info("DEBUG batch_assignees filter SKIPPED (not mentioned in source)")

            assignee_objects: List[TelegramUser] = []
            for raw_name in assignees:
                clean_name = raw_name.lstrip("@").strip()
                if not clean_name:
                    continue
                user: Optional[TelegramUser] = await sync_to_async(_find_user_by_username)(clean_name)
                if user and not _is_bot_user(user):
                    assignee_objects.append(user)
                elif not user:
                    logger.warning("Task: assignee %r not found in DB", raw_name)
                    try:
                        placeholder = await sync_to_async(TelegramUser.objects.create)(
                            telegram_id=-(abs(hash(clean_name)) % 1_000_000_000 + 1_000_000_000),
                            username=clean_name,
                            full_name=clean_name,
                            is_bot=False,
                        )
                        assignee_objects.append(placeholder)
                    except Exception:
                        logger.warning("Failed to create placeholder for %s", clean_name)

            existing = await sync_to_async(self._check_duplicate_task)(
                clean_title, due_date, topic, assignee_objects,
            )
            if existing:
                logger.info("Duplicate task: '%s' due %s, skipping", clean_title, due_date)
                return existing

            task = await sync_to_async(Task.objects.create)(
                title=clean_title,
                description=task_data.get("description", ""),
                topic=topic,
                due_date=due_date,
                source_message=source_message,
                creator=source_message.author,
                status="open",
            )

            for user in assignee_objects:
                logger.info("DEBUG_TASK: Creating TaskAssignee for task_id=%s, user_id=%s, username=%s",
                           task.id if task else "pending", user.id, user.username)
                await sync_to_async(TaskAssignee.objects.create)(task=task, user=user)

            # ════════════════════════════════════════════════════════════
            # Подзадачи (переданы явно, напр. для проекта)
            # ════════════════════════════════════════════════════════════
            subtasks_data = task_data.get("subtasks")
            if subtasks_data:
                try:
                    from core.services.subtask_service import create_subtasks_for_task
                    created = await create_subtasks_for_task(task, subtasks_data)
                    if created:
                        logger.info(
                            "Subtasks created | task_id=%s count=%s",
                            task.id, len(created),
                        )
                except Exception as e:
                    logger.warning("Subtasks creation failed: %s", e)

            # ════════════════════════════════════════════════════════════
            # Приоритет задачи
            # ════════════════════════════════════════════════════════════
            # ═══ Fallback: парсим priority из текста сообщения ═══
            if not task_data.get("priority") and source_message and source_message.text:
                text_lower = source_message.text.lower()
                if any(w in text_lower for w in ["срочно", "срочная", "asap", "быстрее", "как можно"]):
                    task_data["priority"] = "critical"
                elif any(w in text_lower for w in ["важно", "важная", "приоритет"]):
                    task_data["priority"] = "high"
                elif any(w in text_lower for w in ["когда будет время", "свободен", "не срочно"]):
                    task_data["priority"] = "low"

            # ═══ Fix: если due_date сильно в прошлом (>7 дней) — пробуем следующий месяц ═══
            if due_date and due_date < timezone.now() - timedelta(days=7):
                try:
                    new_month = due_date.month + 1
                    new_year = due_date.year
                    if new_month > 12:
                        new_month = 1
                        new_year += 1
                    from calendar import monthrange
                    last_day = monthrange(new_year, new_month)[1]
                    new_day = min(due_date.day, last_day)
                    corrected_due = due_date.replace(year=new_year, month=new_month, day=new_day,
                                                     hour=23, minute=59, second=0, microsecond=0)
                    # ═══ Сохраняем исправленную дату в БД ═══
                    await sync_to_async(Task.objects.filter(id=task.id).update)(due_date=corrected_due)
                    due_date = corrected_due
                    logger.info("Due date corrected | task_id=%s", task.id)
                except (ValueError, AttributeError):
                    pass

            priority_raw = task_data.get("priority")
            if priority_raw:
                valid = {"critical", "high", "normal", "low"}
                if priority_raw.lower() in valid:
                    await sync_to_async(Task.objects.filter(id=task.id).update)(priority=priority_raw.lower())
                    logger.info("Priority set | task_id=%s priority=%s", task.id, priority_raw)

            # ════════════════════════════════════════════════════════════
            # Повторяющиеся задачи: если LLM вернула recurrence
            # ════════════════════════════════════════════════════════════
            recurrence_raw = task_data.get("recurrence")
            if recurrence_raw:
                parsed = parse_recurrence(str(recurrence_raw))
                if parsed:
                    rec_svc = RecurrenceService()
                    await rec_svc.create_recurring_task(
                        task=task,
                        cron_expression=parsed["cron"],
                        human_readable=parsed["human"],
                    )
                    logger.info(
                        "Recurring task created | task_id=%s cron=%s human=%s",
                        task.id, parsed["cron"], parsed["human"],
                    )

            return task

        except Exception as e:
            logger.error("_create_task_from_data error: %s", e, exc_info=True)
            return None

    # ── Проверка дубликата ──────────────────────────────────────

    def _check_duplicate_task(
        self, title: str, due_date, topic, assignees: List[TelegramUser],
    ) -> Optional[Task]:
        possible = Task.objects.filter(
            title=title, due_date=due_date, topic=topic, status="open",
        )
        new_ids = {a.id for a in assignees}
        for task in possible:
            existing_ids = set(task.assignees.values_list("user_id", flat=True))
            if existing_ids == new_ids:
                return task
        return None

    # ── Извлечение ──────────────────────────────────────────────

    async def extract_tasks_from_message(self, message: Message) -> List[Task]:
        now = timezone.localtime(timezone.now())
        tasks_data = await self.llm.extract_tasks_from_message(
            message.text, current_context=now.strftime("%Y-%m-%d %H:%M"),
        )
        created: List[Task] = []
        for task_data in (tasks_data or []):
            task = await self._create_task_from_data(task_data, message)
            if task:
                created.append(task)
        return created

    async def extract_tasks_from_messages_batch(
        self, messages: list["Message"],
    ) -> list[Task]:
        if not messages:
            return []

        messages = sorted(messages, key=lambda m: m.timestamp)
        context_lines = []
        for msg in messages:
            author = (
                f"@{msg.author.username}"
                if msg.author and msg.author.username
                else (msg.author.full_name or str(msg.author.telegram_id))
            )
            time_str = timezone.localtime(msg.timestamp).strftime("%H:%M")
            context_lines.append(f"[{time_str}] {author}: {msg.text}")

        batch_text = "\n".join(context_lines)
        now = timezone.localtime(timezone.now())

        try:
            tasks_data = await self.llm.extract_tasks_from_messages(
                batch_text, current_context=now.strftime("%Y-%m-%d %H:%M"),
            )
        except Exception as e:
            logger.error("Batch task extraction failed: %s", e, exc_info=True)
            return []

        if not tasks_data:
            return []

        created: list[Task] = []
        source_message = messages[-1]
        for task_data in tasks_data:
            task = await self._create_task_from_data(task_data, source_message)
            if task:
                created.append(task)
        return created

    # ── Запросы ─────────────────────────────────────────────────

    async def get_task_by_id(self, task_id: int) -> Optional[Task]:
        def _get():
            return (
                Task.objects.filter(id=task_id)
                .select_related("topic", "creator")
                .prefetch_related("assignees__user", "subtasks__assignees")
                .first()
            )
        return await sync_to_async(_get)()

    async def get_user_tasks(self, user: TelegramUser, status: str = "open") -> List[Task]:
        def _query() -> List[Task]:
            return list(
                Task.objects.filter(assignees__user=user, status=status).order_by("due_date")
            )
        return await sync_to_async(_query)()

    async def mark_task_done(self, task_id: int, user: TelegramUser) -> bool:
        def _update() -> bool:
            try:
                task = Task.objects.get(id=task_id)
                if not TaskAssignee.objects.filter(task=task, user=user).exists():
                    logger.warning("mark_task_done: user %s not assignee of task %s", user.id, task_id)
                    return False
                task.status = "done"
                task.completed_at = timezone.now()
                task.save(update_fields=["status", "completed_at"])
                return True
            except Task.DoesNotExist:
                return False

        result = await sync_to_async(_update)()
        if not result:
            return False

        try:
            task = await sync_to_async(Task.objects.get)(id=task_id)
            rec_svc = RecurrenceService()
            new_task = await rec_svc.on_task_completed(task)
            if new_task:
                logger.info(
                    "Next recurring task created | prev_task_id=%s new_task_id=%s due=%s",
                    task.id, new_task.id, new_task.due_date,
                )
                try:
                    from celery_app.tasks.send_reminders import send_task_assigned_notification
                    send_task_assigned_notification.delay(new_task.id)
                except Exception:
                    pass
        except Exception as e:
            logger.error("Failed to process recurrence for task %s: %s", task_id, e, exc_info=True)

        return True

    # ── Редактирование задачи ────────────────────────────────────

    async def update_due_date(self, task_id: int, new_due_date_str: str) -> bool:
        def _update() -> bool:
            try:
                task = Task.objects.get(id=task_id)
                naive_dt = datetime.strptime(str(new_due_date_str)[:10], "%Y-%m-%d")
                naive_dt = naive_dt.replace(hour=23, minute=59, second=0, microsecond=0)
                current_tz = timezone.get_current_timezone()
                task.due_date = timezone.make_aware(naive_dt, current_tz)
                task.save(update_fields=["due_date"])
                return True
            except (Task.DoesNotExist, ValueError, TypeError) as e:
                logger.warning("update_due_date failed: %s", e)
                return False
        return await sync_to_async(_update)()

    async def update_assignees(
        self, task_id: int, assignee_usernames: List[str],
    ) -> bool:
        def _update() -> bool:
            try:
                task = Task.objects.get(id=task_id)
                TaskAssignee.objects.filter(task=task).delete()
                for raw_name in assignee_usernames:
                    clean_name = raw_name.lstrip("@").strip()
                    if not clean_name:
                        continue
                    user = _find_user_by_username(clean_name)
                    if user and not _is_bot_user(user):
                        TaskAssignee.objects.create(task=task, user=user)
                return True
            except Task.DoesNotExist:
                return False
        return await sync_to_async(_update)()

    async def update_title(self, task_id: int, new_title: str) -> bool:
        def _update() -> bool:
            try:
                task = Task.objects.get(id=task_id)
                clean = _clean_title(new_title)
                if not clean:
                    return False
                task.title = clean
                task.save(update_fields=["title"])
                return True
            except Task.DoesNotExist:
                return False
        return await sync_to_async(_update)()

    async def get_overdue_tasks(self) -> List[Task]:
        def _query() -> List[Task]:
            return list(
                Task.objects.filter(status="open", due_date__lt=timezone.now())
                .select_related("topic")
                .prefetch_related("assignees__user")
            )
        return await sync_to_async(_query)()


def _is_author_mentioned_in_batch(assignees: List[str], source_message: Message) -> bool:
    if not source_message or not source_message.text:
        return True
    text_lower = source_message.text.lower()
    for raw in assignees:
        clean = raw.lstrip("@").strip().lower()
        if clean and clean in text_lower:
            return True
    return False


async def create_project_task_from_text(source_message: Message) -> Optional[Task]:
    """Если source_message.text — 'проект: a, b, c', создаёт ОДНУ задачу-родителя
    с подзадачами и возвращает её. Иначе None.

    Работает и для группы, и для ЛС: берём ТЕКСТ исходного сообщения
    (а не то, что вернул LLM — он может разбить проект на 5 отдельных задач).
    """
    from core.services.subtask_service import (
        _split_project_title,
        _parse_sub_item,
    )

    text = (getattr(source_message, "text", None) or "").strip()
    parent, items = _split_project_title(text)
    if not parent or len(items) < 2:
        return None

    clean_title = _clean_project_parent(parent) or _clean_title(text)
    if not clean_title:
        return None

    # исполнители родителя — @username из текста до двоеточия проекта
    parent_assignees = ["@" + u for u in re.findall(r"@(\w+)", parent)]
    if not parent_assignees:
        parent_assignees = ["@" + u for u in re.findall(r"@(\w+)", text)]

    subtasks_data = []
    max_due = None
    for it in items:
        t, users, due = _parse_sub_item(it)
        if not t:
            continue
        subtasks_data.append({
            "title": t,
            "assignees": ["@" + u for u in users],
            "due_date": due,
        })
        if due and (max_due is None or due > max_due):
            max_due = due
    if not subtasks_data:
        return None

    ts = TaskService()
    task_data = {
        "title": clean_title,
        "assignees": list(dict.fromkeys(parent_assignees)),
        "due_date": max_due.strftime("%Y-%m-%d") if max_due else None,
        "description": "",
        "subtasks": subtasks_data,
    }
    task = await ts._create_task_from_data(task_data, source_message)
    return task
