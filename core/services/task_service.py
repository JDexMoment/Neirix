import logging
from datetime import datetime
from typing import List, Optional
from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async
from core.models import Task, Topic, TelegramUser, Message
from core.utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self):
        self.llm = LLMClient()

    async def extract_tasks_from_message(self, message: Message) -> List[Task]:
        tasks_data = await self.llm.extract_tasks_from_message(message.text)
        created_tasks = []
        for task_data in tasks_data:
            try:
                assignee = None
                if task_data.get('assignee'):
                    assignee = await sync_to_async(
                        lambda: TelegramUser.objects.filter(
                            Q(username__iexact=task_data['assignee']) |
                            Q(full_name__icontains=task_data['assignee'])
                        ).first()
                    )()

                due_date = None
                if task_data.get('due_date'):
                    try:
                        due_date = datetime.strptime(task_data['due_date'], '%Y-%m-%d')
                    except ValueError:
                        pass

                task = await sync_to_async(Task.objects.create)(
                    title=task_data['title'],
                    description=task_data.get('description', ''),
                    topic=message.topic,
                    assignee=assignee,
                    due_date=due_date,
                    source_message=message,
                    status='open'
                )
                created_tasks.append(task)
                logger.info(f"Created task {task.id} from message {message.id}")
            except Exception as e:
                logger.error(f"Failed to create task from extracted data: {e}")
        return created_tasks

    def get_user_tasks(self, user: TelegramUser, status: str = 'open') -> List[Task]:
        return Task.objects.filter(assignee=user, status=status).order_by('due_date')

    def mark_task_done(self, task_id: int, user: TelegramUser) -> bool:
        try:
            task = Task.objects.get(id=task_id)
            task.status = 'done'
            task.save()
            logger.info(f"Task {task_id} marked as done by {user}")
            return True
        except Task.DoesNotExist:
            return False

    def get_overdue_tasks(self) -> List[Task]:
        return Task.objects.filter(
            status='open',
            due_date__lt=timezone.now()
        ).select_related('topic', 'assignee')