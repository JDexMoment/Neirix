import logging
import re
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import Task, TelegramUser, Message, TaskAssignee

if TYPE_CHECKING:
    from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _is_bot_user(user: TelegramUser) -> bool:
    if hasattr(user, "is_bot") and user.is_bot:
        return True
    if user.username and re.search(r"[_]?[Bb]ot$", user.username):
        return True
    return False


def _find_user_by_username(clean_name: str) -> Optional[TelegramUser]:
    # Ищем пользователя по username (без учета регистра)
    user = TelegramUser.objects.filter(
        username__iexact=clean_name
    ).first()
    
    if user:
        return user
    
    # Если не найден по username, ищем по полному имени (точное совпадение)
    user = TelegramUser.objects.filter(
        full_name__iexact=clean_name
    ).first()
    
    if user:
        return user
    
    # Если имя состоит из двух слов, пробуем найти по частям
    name_parts = clean_name.split()
    if len(name_parts) == 2:
        user = TelegramUser.objects.filter(
            Q(full_name__icontains=name_parts[0]) & Q(full_name__icontains=name_parts[1])
        ).first()
        
        if user:
            return user
    
    # Поиск по частичному совпадению в полном имени
    user = TelegramUser.objects.filter(
        full_name__icontains=clean_name
    ).first()
    
    if user:
        return user
        
    # Поиск по первому имени или фамилии (если имя содержит пробел)
    if ' ' in clean_name:
        first_name, last_name = clean_name.split(' ', 1)
        user = TelegramUser.objects.filter(
            Q(full_name__icontains=first_name) | Q(full_name__icontains=last_name)
        ).first()
        
        if user:
            return user
    
    # Поиск по самому длинному совпадению (на случай сокращений)
    possible_matches = TelegramUser.objects.filter(
        full_name__icontains=clean_name.split()[0] if clean_name.split() else clean_name
    )
    
    for match in possible_matches:
        if clean_name.lower() in match.full_name.lower() or match.full_name.lower() in clean_name.lower():
            return match
    
    return None


class TaskService:
    def __init__(self, llm: Optional["LLMClient"] = None):
        self._llm = llm

    @property
    def llm(self) -> "LLMClient":
        if self._llm is None:
            from core.utils.llm_client import LLMClient
            self._llm = LLMClient()
        return self._llm

    def _is_bot_user(self, user: TelegramUser) -> bool:
        """Check if the user is a bot."""
        return user.is_bot

    async def _create_task_from_data(
        self,
        task_data: dict,
        source_message: Message,
    ) -> Optional[Task]:
        try:
            title = (task_data.get("title") or "").strip()
            if not title:
                logger.warning(
                    "_create_task_from_data: empty title in task_data=%s, skipping",
                    task_data,
                )
                return None

            # Удаляем упоминания пользователей из заголовка задачи
            import re
            username_pattern = re.compile(r'@\w+')
            clean_title = username_pattern.sub('', title).strip()
            # Убираем лишние пробелы и возможные остатки символов
            clean_title = re.sub(r'\s+', ' ', clean_title).strip()
            # Убираем лишние символы в начале и конце
            clean_title = re.sub(r'^[,\-\s\|]+|[,\-\s\|]+$', '', clean_title).strip()
            
            if not clean_title:
                logger.warning("_create_task_from_data: title became empty after removing mentions, skipping")
                return None

            due_date = None
            raw_due = task_data.get("due_date")
            if raw_due:
                try:
                    naive_dt = datetime.strptime(raw_due, "%Y-%m-%d")
                    naive_dt = naive_dt.replace(
                        hour=23,
                        minute=59,
                        second=0,
                        microsecond=0,
                    )
                    current_tz = timezone.get_current_timezone()
                    due_date = timezone.make_aware(naive_dt, current_tz)
                except ValueError:
                    logger.warning(
                        "_create_task_from_data: invalid due_date format %r, ignoring",
                        raw_due,
                    )

            task = await sync_to_async(Task.objects.create)(
                title=clean_title,
                description=task_data.get("description", ""),
                topic=source_message.topic,
                due_date=due_date,
                source_message=source_message,
                status="open",
                creator=source_message.author,  # Устанавливаем создателя как автора сообщения
            )

            assignees: List[str] = task_data.get("assignees", [])
            
            # Проверяем, упомянут ли автор сообщения в тексте сообщения
            message_text_lower = source_message.text.lower()
            author_mentioned = False
            
            if source_message.author.username:
                author_mentioned = f"@{source_message.author.username}".lower() in message_text_lower
            
            # Если автор не упомянут в сообщении, удаляем его из списка исполнителей
            if not author_mentioned:
                assignees = [
                    a for a in assignees 
                    if source_message.author.username and a.lower() != f"@{source_message.author.username}".lower()
                ]
            for raw_name in assignees:
                clean_name = raw_name.lstrip("@").strip()
                if not clean_name:
                    continue

                user: Optional[TelegramUser] = await sync_to_async(
                    _find_user_by_username
                )(clean_name)

                if user:
                    # Skip the message author unless they are a bot
                    if user.telegram_id == source_message.author.telegram_id:
                        if not self._is_bot_user(user):
                            continue
                    await sync_to_async(TaskAssignee.objects.create)(
                        task=task,
                        user=user,
                    )
                else:
                    logger.warning(
                        "Task id=%s: assignee %r not found in DB, skipping",
                        task.id,
                        raw_name,
                    )

            return task

        except Exception as e:
            logger.error(
                "_create_task_from_data: task creation failed for task_data=%s: %s",
                task_data,
                e,
                exc_info=True,
            )
            return None

    async def extract_tasks_from_message(self, message: Message) -> List[Task]:
        now = timezone.localtime(timezone.now())
        context_str = now.strftime("%Y-%m-%d %H:%M")

        tasks_data = await self.llm.extract_tasks_from_message(
            message.text,
            current_context=context_str,
        )

        created_tasks: List[Task] = []

        for task_data in tasks_data:
            task = await self._create_task_from_data(task_data, message)
            if task:
                created_tasks.append(task)

        return created_tasks

    async def extract_tasks_from_messages_batch(
        self,
        messages: list["Message"],
    ) -> list[Task]:
        """
        Извлекает задачи из пачки сообщений одним вызовом LLM.
        """
        if not messages:
            return []

        messages = sorted(messages, key=lambda m: m.timestamp)

        context_lines = []
        for msg in messages:
            author = (
                f"@{msg.author.username}"
                if msg.author and msg.author.username
                else (
                    msg.author.full_name
                    or str(msg.author.telegram_id)
                )
            )
            time_str = timezone.localtime(msg.timestamp).strftime("%H:%M")
            context_lines.append(f"[{time_str}] {author}: {msg.text}")

        batch_text = "\n".join(context_lines)

        now = timezone.localtime(timezone.now())
        context_str = now.strftime("%Y-%m-%d %H:%M")

        try:
            tasks_data = await self.llm.extract_tasks_from_messages(
                batch_text,
                current_context=context_str,
            )
        except Exception as e:
            logger.error("Batch task extraction LLM call failed: %s", e, exc_info=True)
            return []

        if not tasks_data:
            return []

        created_tasks: list[Task] = []
        source_message = messages[-1]

        for task_data in tasks_data:
            task = await self._create_task_from_data(task_data, source_message)
            if task:
                created_tasks.append(task)

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