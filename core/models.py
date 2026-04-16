from django.db import models
import uuid

class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class TelegramChat(models.Model):
    chat_id = models.BigIntegerField(unique=True)
    title = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=20)  # supergroup, channel, private
    is_forum = models.BooleanField(default=False)
    link_code = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)  # код для привязки

    def __str__(self):
        return f"{self.title} ({self.chat_id})"


class Topic(models.Model):
    chat = models.ForeignKey(TelegramChat, on_delete=models.CASCADE, related_name='topics')
    thread_id = models.BigIntegerField()
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('chat', 'thread_id')

    def __str__(self):
        return f"Topic {self.thread_id} in {self.chat}"


class TelegramUser(models.Model):
    telegram_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=100, blank=True)
    full_name = models.CharField(max_length=255)
    is_bot = models.BooleanField(default=False)
    # связь с пользователем Django (опционально)
    user = models.OneToOneField('auth.User', on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.full_name} (@{self.username})"


class UserRole(models.Model):
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE)
    chat = models.ForeignKey(TelegramChat, on_delete=models.CASCADE)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, null=True, blank=True)
    role = models.CharField(max_length=50, choices=[
        ('member', 'Участник'),
        ('manager', 'Менеджер'),
        ('admin', 'Админ')
    ])

    class Meta:
        unique_together = ('user', 'chat')


class Message(models.Model):
    telegram_msg_id = models.BigIntegerField()
    chat = models.ForeignKey(TelegramChat, on_delete=models.CASCADE)
    topic = models.ForeignKey(Topic, on_delete=models.SET_NULL, null=True, blank=True)
    author = models.ForeignKey(TelegramUser, on_delete=models.CASCADE)
    text = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField()
    is_processed = models.BooleanField(default=False)
    # дополнительные поля для хранения медиа (можно добавить позже)

    class Meta:
        unique_together = ('chat', 'topic', 'telegram_msg_id')
        indexes = [
            models.Index(fields=['chat', 'topic', '-timestamp']),
            models.Index(fields=['is_processed', 'timestamp']),
        ]

    def __str__(self):
        return f"Msg {self.telegram_msg_id} from {self.author} at {self.timestamp}"


class Task(models.Model):
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    assignee = models.ForeignKey(TelegramUser, on_delete=models.SET_NULL, null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='open', choices=[
        ('open', 'В работе'),
        ('done', 'Выполнено'),
        ('cancelled', 'Отменено')
    ])
    source_message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['assignee', 'status']),
            models.Index(fields=['topic', 'status']),
            models.Index(fields=['due_date', 'status']),
        ]

    def __str__(self):
        return self.title


class Meeting(models.Model):
    title = models.CharField(max_length=300)
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    start_at = models.DateTimeField()
    reminder_sent = models.BooleanField(default=False)
    participants = models.ManyToManyField(TelegramUser, blank=True)
    source_message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.title} at {self.start_at}"


class Summary(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    content = models.TextField()
    generated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Summary for {self.topic} ({self.period_start.date()} - {self.period_end.date()})"