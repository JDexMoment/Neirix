"""
Хендлеры для комментариев к задачам и встречам.

- Ответ на сообщение с задачей → добавляет комментарий (в messages.py)
- /task info N → показывает задачу + комментарии
- /meeting info N → показывает встречу + комментарии
- Кнопка 💬 (n) → показывает элемент + комментарии
"""
import logging
import re

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.db.models import Count

from core.models import Task, Meeting, Comment, TelegramUser
from bot.handlers.tasks import _build_task_text
from bot.handlers.meetings import _build_meeting_text

logger = logging.getLogger(__name__)
router = Router()


@sync_to_async
def _add_comment(task_id: int = None, meeting_id: int = None, author_id: int = None, text: str = None):
    try:
        author = TelegramUser.objects.filter(telegram_id=author_id).first()
        if not author or not text:
            return None
        kw = {"author": author, "text": text.strip()}
        if task_id:
            task = Task.objects.filter(id=task_id).first()
            if not task:
                return None
            kw["task"] = task
        elif meeting_id:
            meeting = Meeting.objects.filter(id=meeting_id).first()
            if not meeting:
                return None
            kw["meeting"] = meeting
        else:
            return None
        return Comment.objects.create(**kw)
    except Exception as e:
        logger.error("Add comment error: %s", e, exc_info=True)
        return None


@sync_to_async
def _get_comments(task_id: int = None, meeting_id: int = None):
    kw = {}
    if task_id:
        kw["task_id"] = task_id
    elif meeting_id:
        kw["meeting_id"] = meeting_id
    else:
        return []
    return list(Comment.objects.filter(**kw).select_related("author").order_by("created_at"))


@sync_to_async
def _get_comment_counts(task_ids: list = None, meeting_ids: list = None):
    result = {}
    if task_ids:
        qs = Comment.objects.filter(task_id__in=task_ids).values('task_id').annotate(cnt=Count('id'))
        for item in qs:
            result[item['task_id']] = item['cnt']
    if meeting_ids:
        qs = Comment.objects.filter(meeting_id__in=meeting_ids).values('meeting_id').annotate(cnt=Count('id'))
        for item in qs:
            result[item['meeting_id']] = item['cnt']
    return result


@sync_to_async
def _find_task_by_title(chat_id: int, title: str):
    return Task.objects.filter(
        topic__chat__chat_id=chat_id,
        title__icontains=title,
    ).order_by("-id").first()


@sync_to_async
def _find_meeting_by_title(chat_id: int, title: str):
    return Meeting.objects.filter(
        topic__chat__chat_id=chat_id,
        title__icontains=title,
    ).order_by("-id").first()


def _extract_title_from_msg(text: str) -> str:
    """Извлекает название из plain text сообщения бота."""
    if not text:
        return ""
    # Для встреч: '• интервью у вписки ⏰ ...'
    m = re.search(r'[•\-]\s*(.+?)(?:\s+⏰|\n|$)', text)
    if m:
        return m.group(1).strip()
    # Для задач: '1. повторить материал\n👤 ...'
    m = re.search(r'(?:\d+\.\s*)(.+?)(?:\s*\n|\s*$)', text)
    if m:
        candidate = m.group(1).strip()
        if candidate and len(candidate) > 2:
            return candidate
    return ""


def _format_comments(comments: list) -> str:
    if not comments:
        return "\n💬 Нет комментариев."
    lines = ["\n💬 <b>Комментарии:</b>"]
    for c in comments:
        author = f"@{c.author.username}" if c.author.username else c.author.full_name
        dt = timezone.localtime(c.created_at).strftime("%d.%m %H:%M")
        lines.append(f"  {author} ({dt}):")
        lines.append(f"  {c.text}")
    return "\n".join(lines)


# ── Callback для кнопки 💬 (n) ──

@router.callback_query(F.data.startswith("task_info:"))
async def callback_task_info(callback: CallbackQuery):
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID.", show_alert=True)
        return

    task = await sync_to_async(
        lambda: Task.objects.filter(id=task_id)
        .select_related("creator")
        .prefetch_related("assignees__user", "subtasks__assignees")
        .first()
    )()
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    comments = await _get_comments(task_id=task_id)
    await callback.message.answer(
        f"{_build_task_text(task)}{_format_comments(comments)}",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("meeting_info:"))
async def callback_meeting_info(callback: CallbackQuery):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID.", show_alert=True)
        return

    meeting = await sync_to_async(
        lambda: Meeting.objects.filter(id=meeting_id)
        .select_related("creator", "recurrence")
        .prefetch_related("participants")
        .first()
    )()
    if not meeting:
        await callback.answer("Встреча не найдена.", show_alert=True)
        return

    comments = await _get_comments(meeting_id=meeting_id)
    await callback.message.answer(
        f"{_build_meeting_text(meeting)}{_format_comments(comments)}",
        parse_mode="HTML",
    )
    await callback.answer()


# ── Команды ──

@router.message(Command("task_info"))
async def cmd_task_info(message: Message):
    text = message.text or ""
    parts = text.split()
    task_id = None
    for p in parts:
        try:
            task_id = int(p)
            break
        except ValueError:
            continue

    if not task_id:
        await message.answer("Укажите ID задачи: /task_info 3")
        return

    task = await sync_to_async(
        lambda: Task.objects.filter(id=task_id)
        .select_related("creator")
        .prefetch_related("assignees__user", "subtasks__assignees")
        .first()
    )()
    if not task:
        await message.answer(f"❌ Задача #{task_id} не найдена.")
        return

    comments = await _get_comments(task_id=task_id)
    await message.answer(
        f"{_build_task_text(task)}{_format_comments(comments)}",
        parse_mode="HTML",
    )


@router.message(Command("meeting_info"))
async def cmd_meeting_info(message: Message):
    text = message.text or ""
    parts = text.split()
    meeting_id = None
    for p in parts:
        try:
            meeting_id = int(p)
            break
        except ValueError:
            continue

    if not meeting_id:
        await message.answer("Укажите ID встречи: /meeting_info 3")
        return

    meeting = await sync_to_async(
        lambda: Meeting.objects.filter(id=meeting_id)
        .select_related("creator", "recurrence")
        .prefetch_related("participants")
        .first()
    )()
    if not meeting:
        await message.answer(f"❌ Встреча #{meeting_id} не найдена.")
        return

    comments = await _get_comments(meeting_id=meeting_id)
    await message.answer(
        f"{_build_meeting_text(meeting)}{_format_comments(comments)}",
        parse_mode="HTML",
    )
