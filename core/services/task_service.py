import logging
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import Task, TelegramUser, Message, TaskAssignee

if TYPE_CHECKING:
    from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _find_user_by_username(clean_name: str) -> Optional[TelegramUser]:
    return TelegramUser.objects.filter(
        Q(username__iexact=clean_name) | Q(full_name__icontains=clean_name)
    ).first()


class TaskService:
    def __init__(self, llm: Optional["LLMClient"] = None):
        self._llm = llm

    @property
    def llm(self) -> "LLMClient":
        if self._llm is None:
            from core.utils.llm_client import LLMClient
            self._llm = LLMClient()
        return self._llm

    async def extract_tasks_from_message(self, message: Message) -> List[Task]:
        now = timezone.localtime(timezone.now())
        context_str = now.strftime("%Y-%m-%d %H:%M")

        tasks_data = await self.llm.extract_tasks_from_message(
            message.text,
            current_context=context_str,
        )

        created_tasks: List[Task] = []

        for task_data in tasks_data:
            try:
                title = (task_data.get("title") or "").strip()
                if not title:
                    logger.warning(
                        "extract_tasks_from_message: empty title in task_data=%s, skipping",
                        task_data,
                    )
                    continue

                due_date = None
                raw_due = task_data.get("due_date")
                if raw_due:
                    try:
                        naive_dt = datetime.strptime(raw_due, "%Y-%m-%d")
                        current_tz = timezone.get_current_timezone()
                        naive_dt = naive_dt.replace(hour=23, minute=59, second=0)
                        due_date = timezone.make_aware(naive_dt, current_tz)
                    except ValueError:
                        logger.warning(
                            "extract_tasks_from_message: invalid due_date format %r, ignoring",
                            raw_due,
                        )

                task = await sync_to_async(Task.objects.create)(
                    title=title,
                    description=task_data.get("description", ""),
                    topic=message.topic,
                    due_date=due_date,
                    source_message=message,
                    status="open",
                )

                assignees: List[str] = task_data.get("assignees", [])
                for raw_name in assignees:
                    clean_name = raw_name.lstrip("@").strip()
                    if not clean_name:
                        continue

                    user: Optional[TelegramUser] = await sync_to_async(_find_user_by_username)(clean_name)

                    if user:
                        await sync_to_async(TaskAssignee.objects.create)(
                            task=task,
                            user=user,
                        )
                    else:
                        logger.warning(
                            "Task id=%s: assignee %r not found in DB, skipping",
                            task.id, raw_name,
                        )

                created_tasks.append(task)

            except Exception as e:
                logger.error(
                    "extract_tasks_from_message: task creation failed for task_data=%s: %s",
                    task_data, e, exc_info=True,
                )

        return created_tasks

    async def get_user_tasks(self, user: TelegramUser, status: str = "open") -> List[Task]:
        def _query() -> List[Task]:
            return list(
                Task.objects.filter(
                    assignees__user=user,
                    status=status,
                ).order_by("due_date")
            )
        return await sync_to_async(_query)()

    async def mark_task_done(self, task_id: int, user: TelegramUser) -> bool:
        def _update() -> bool:
            try:
                task = Task.objects.get(id=task_id)

                is_assignee = TaskAssignee.objects.filter(
                    task=task,
                    user=user,
                ).exists()

                if not is_assignee:
                    logger.warning(
                        "mark_task_done: user %s is not assignee of task %s, denied",
                        user.id, task_id,
                    )
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
                Task.objects.filter(
                    status="open",
                    due_date__lt=timezone.now(),
                )
                .select_related("topic")
                .prefetch_related("assignees__user")
            )
        return await sync_to_async(_query)()