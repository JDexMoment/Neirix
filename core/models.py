from django.db import models
import uuid
from datetime import time as dtime


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
    due_date = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, default='open', choices=[
        ('open', 'В работе'),
        ('done', 'Выполнено'),
        ('cancelled', 'Отменено')
    ])

    source_message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата выполнения")
    # Creator field - who created the task
    creator = models.ForeignKey(TelegramUser, on_delete=models.SET_NULL, null=True, related_name='created_tasks')
    # Напоминание за сутки до дедлайна
    daily_reminder_sent = models.BooleanField(default=False)
    # Напоминание о просрочке 
    overdue_reminder_sent = models.BooleanField(default=False)

     # ── Повторяющиеся задачи ──
    is_template = models.BooleanField(
        default=False,
        verbose_name="Шаблон серии",
        help_text="True = задача-шаблон для повторяющейся серии",
    )
    recurrence_group_id = models.UUIDField(
        null=True, blank=True,
        verbose_name="ID группы повторений",
        help_text="Все задачи одной серии имеют одинаковый group_id",
    )

    class Meta:
        indexes = [
            models.Index(fields=['topic', 'status']),
            models.Index(fields=['due_date', 'status']),
        ]

    def __str__(self):
        return self.title
    
    def reset_reminders(self):
        """Сбрасывает флаги напоминаний (при изменении дедлайна)."""
        self.daily_reminder_sent = False
        self.overdue_reminder_sent = False
    

class TaskAssignee(models.Model):
    task = models.ForeignKey('Task', on_delete=models.CASCADE, related_name='assignees')
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('task', 'user')

    def __str__(self):
        return f"{self.user} -> {self.task}"


class Meeting(models.Model):
    STATUS_CHOICES = [
        ('active', 'Активна'),
        ('cancelled', 'Отменена'),
        ('rescheduled', 'Перенесена'),
    ]

    status = models.CharField(
        max_length=20,
        default='active',
        choices=STATUS_CHOICES,
    )

    title = models.CharField(max_length=300)
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    start_at = models.DateTimeField()
    participants = models.ManyToManyField(TelegramUser, blank=True)

    is_all_hands = models.BooleanField(
        default=False,
        verbose_name="Все участники",
        help_text="True, если встреча для всех участников чата",
    )

    source_message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True)
    # Creator field - who created the meeting
    creator = models.ForeignKey(TelegramUser, on_delete=models.SET_NULL, null=True, related_name='created_meetings')

    # Напоминание за час до встречи
    reminder_sent = models.BooleanField(default=False)
    # Напоминание за сутки до встречи
    daily_reminder_sent = models.BooleanField(default=False)

    # ── Связь с повторяющейся серией ──
    recurrence = models.ForeignKey(
        'MeetingRecurrence',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='instances',
        verbose_name="Серия повторений",
    )

    def __str__(self):
        return f"{self.title} at {self.start_at}"

    def reset_reminders(self):
        self.reminder_sent = False
        self.daily_reminder_sent = False


class Summary(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    content = models.TextField()
    generated_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Summary for {self.topic} ({self.period_start.date()} - {self.period_end.date()})"
    

class TaskRecurrence(models.Model):
    """
    Привязка к задаче-шаблону.
    Когда задача с TaskRecurrence отмечается как done,
    создаётся следующая задача по расписанию.
    """
    task = models.OneToOneField(
        Task, on_delete=models.CASCADE,
        related_name='recurrence',
        verbose_name="Задача-шаблон",
    )
    cron_expression = models.CharField(
        max_length=100,
        verbose_name="Cron-выражение",
        help_text="Пример: '0 18 * * 5' — каждая пятница в 18:00",
    )
    human_readable = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="Человекочитаемое описание",
        help_text="Пример: 'каждую пятницу в 18:00'",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна",
    )
    next_occurrence = models.DateTimeField(
        verbose_name="Следующее выполнение",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано",
    )

    class Meta:
        verbose_name = "Повторение задачи"
        verbose_name_plural = "Повторения задач"
        ordering = ["-next_occurrence"]

    def __str__(self):
        return f"TaskRecurrence({self.task.title}, {self.human_readable or self.cron_expression})"


# ──┐
#   ├─ Повторяющиеся встречи
#   └─

class MeetingRecurrence(models.Model):
    """
    Шаблон повторяющейся встречи.
    Хранит расписание и создаёт инстансы Meeting заранее.
    """
    title = models.CharField(max_length=500, verbose_name="Название")
    description = models.TextField(blank=True, default="", verbose_name="Описание")
    topic = models.ForeignKey(
        Topic, on_delete=models.CASCADE,
        verbose_name="Топик",
    )
    creator = models.ForeignKey(
        TelegramUser, null=True, on_delete=models.SET_NULL,
        verbose_name="Создатель",
    )
    cron_expression = models.CharField(
        max_length=100,
        verbose_name="Cron-выражение",
        help_text="Пример: '0 10 * * 1' — каждый понедельник в 10:00",
    )
    human_readable = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="Человекочитаемое описание",
        help_text="Пример: 'каждый понедельник в 10:00'",
    )
    duration_minutes = models.IntegerField(
        default=60,
        verbose_name="Длительность (мин)",
    )
    is_all_hands = models.BooleanField(
        default=False,
        verbose_name="Для всех участников",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна",
    )
    source_message = models.ForeignKey(
        Message, null=True, on_delete=models.SET_NULL,
        verbose_name="Исходное сообщение",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано",
    )

    class Meta:
        verbose_name = "Серия встреч"
        verbose_name_plural = "Серии встреч"
        ordering = ["-created_at"]

    def __str__(self):
        return f"MeetingRecurrence({self.title}, {self.human_readable or self.cron_expression})"


class MeetingRecurrenceParticipant(models.Model):
    """Участники повторяющейся встречи (применяются ко всем инстансам)."""
    recurring_meeting = models.ForeignKey(
        MeetingRecurrence, on_delete=models.CASCADE,
        related_name='participant_links',
    )
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Участник серии встреч"
        verbose_name_plural = "Участники серий встреч"
        unique_together = [("recurring_meeting", "user")]

    def __str__(self):
        return f"{self.user} → {self.recurring_meeting.title}"
    

class UserNotificationSettings(models.Model):
    """Настройки уведомлений для каждого пользователя."""
    user = models.OneToOneField(
        TelegramUser, on_delete=models.CASCADE,
        related_name='notif_settings',
    )
    meeting_reminder_minutes = models.IntegerField(
        default=60,
        choices=[(15, '15 минут'), (60, '1 час'), (1440, '1 день')],
        verbose_name="Напоминание о встрече",
    )
    digest_time = models.TimeField(
        null=True, blank=True, default=dtime(9, 0),
        verbose_name="Время дайджеста",
    )
    digest_enabled = models.BooleanField(default=True, verbose_name="Дайджест включён")
    task_reminder_enabled = models.BooleanField(default=True, verbose_name="Напоминания о задачах")
    meeting_reminder_enabled = models.BooleanField(default=True, verbose_name="Напоминания о встречах")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки уведомлений"
        verbose_name_plural = "Настройки уведомлений"

    def __str__(self):
        return f"Settings for user {self.user}"


class MeetingAttendance(models.Model):
    """Статус подтверждения участия во встрече."""
    PENDING = 'pending'
    CONFIRMED = 'confirmed'
    DECLINED = 'declined'

    STATUS_CHOICES = [
        (PENDING, 'Ожидание'),
        (CONFIRMED, 'Подтверждено'),
        (DECLINED, 'Отказ'),
    ]

    meeting = models.ForeignKey(
        Meeting, on_delete=models.CASCADE,
        related_name='attendances',
    )
    user = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE,
        related_name='meeting_attendances',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default=PENDING,
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Подтверждение участия"
        verbose_name_plural = "Подтверждения участия"
        unique_together = ('meeting', 'user')

    def __str__(self):
        return f"{self.user} → {self.meeting.title}: {self.status}"


class Comment(models.Model):
    """Комментарий к задаче или встрече."""
    task = models.ForeignKey(
        Task, null=True, blank=True, on_delete=models.CASCADE,
        related_name='comments', verbose_name="Задача",
    )
    meeting = models.ForeignKey(
        Meeting, null=True, blank=True, on_delete=models.CASCADE,
        related_name='comments', verbose_name="Встреча",
    )
    author = models.ForeignKey(
        TelegramUser, on_delete=models.CASCADE,
        verbose_name="Автор",
    )
    text = models.TextField(verbose_name="Текст комментария")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Комментарий"
        verbose_name_plural = "Комментарии"
        ordering = ['created_at']

    def __str__(self):
        target = self.task.title if self.task else (self.meeting.title if self.meeting else "?")
        return f"💬 {self.author} → {target}: {self.text[:50]}"