import logging
import re
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import Task, TelegramUser, Message, TaskAssignee, Topic, UserRole
from core.services.permissions import user_can_create

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
    """
    Если source_message пришёл из приватного чата, находит Topic
    привязанной группы (через UserRole). Иначе возвращает исходный topic.
    """
    chat = source_message.chat
    if chat.type != "private":
        return source_message.topic

    author = source_message.author
    if not author:
        return source_message.topic

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

            # ════════════════════════════════════════════════════════
            # Проверка прав: member не может создавать задачи
            # ════════════════════════════════════════════════════════
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
            assignees = _filter_author_from_list(assignees, source_message)

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
                await sync_to_async(TaskAssignee.objects.create)(task=task, user=user)

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
                task.save(update_fields=["status"])
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
