from django.contrib import admin
from .models import (
    Department, TelegramChat, Topic, TelegramUser,
    UserRole, Message, Task, Meeting, Summary
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)


@admin.register(TelegramChat)
class TelegramChatAdmin(admin.ModelAdmin):
    list_display = ('chat_id', 'title', 'type', 'is_forum', 'link_code')
    search_fields = ('title', 'chat_id')
    readonly_fields = ('link_code',)


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ('id', 'chat', 'thread_id', 'department', 'is_active')
    list_filter = ('is_active', 'chat', 'department')
    search_fields = ('thread_id',)


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('telegram_id', 'username', 'full_name', 'is_bot')
    search_fields = ('username', 'full_name', 'telegram_id')
    list_filter = ('is_bot',)


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ('user', 'chat', 'department', 'role')
    list_filter = ('role', 'chat')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('telegram_msg_id', 'chat', 'topic', 'author', 'timestamp', 'is_processed')
    list_filter = ('is_processed', 'chat', 'topic')
    search_fields = ('text', 'author__username')
    date_hierarchy = 'timestamp'


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'topic', 'assignee', 'due_date', 'status', 'created_at')
    list_filter = ('status', 'topic')
    search_fields = ('title', 'description', 'assignee__username')
    date_hierarchy = 'due_date'


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ('title', 'topic', 'start_at', 'reminder_sent')
    list_filter = ('reminder_sent', 'topic')
    filter_horizontal = ('participants',)
    date_hierarchy = 'start_at'


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ('topic', 'period_start', 'period_end', 'generated_at')
    list_filter = ('topic',)
    date_hierarchy = 'generated_at'