"""
Поиск по задачам и встречам через команду /search.

Работает и в ЛС, и в групповых чатах.
В ЛС — ищет задачи, где пользователь ответственный, и встречи, где он участник.
В группе — ищет в привязанном чате.

NLP-поиск (в ЛС через GigaChat) уже реализован в nlp_processor.py.
Этот модуль — для команды /search (работает везде, без LLM).
"""
import logging
import re
from typing import List, Optional
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from asgiref.sync import sync_to_async
from django.db.models import Q
from django.utils import timezone

from bot.utils import get_chat_context
from core.models import Task, Meeting, TelegramUser
from bot.handlers.tasks import _build_task_text
from bot.handlers.meetings import (
    _build_meeting_text,
    _load_attendance_icons,
    _has_recurrence,
)
from bot.handlers.comments import _get_comment_counts
from bot.keyboards.inline import task_keyboard, meeting_keyboard

logger = logging.getLogger(__name__)
router = Router()

_USERNAME_RE = re.compile(r"@(\w+)")
_NOISE_WORDS = frozenset({
    "и", "в", "на", "с", "по", "the", "and", "with", "in", "on",
    "для", "от", "к", "у", "за", "из", "о", "об",
})
MAX_RESULTS = 10
_USERNAME_RE = re.compile(r"@\w+")  # ← захватывает С @

# ═══════════════════════════════════════════════════════════════
# Парсинг поискового запроса
# ═══════════════════════════════════════════════════════════════
def _parse_search_query(text: str) -> dict:
    text = (text or "").strip()
    lower = text.lower()

    scope = "all"
    if lower.startswith("tasks ") or lower.startswith("задачи "):
        scope = "tasks"
        text = text.split(" ", 1)[1] if " " in text else ""
        lower = text.lower()
    elif lower.startswith("meetings ") or lower.startswith("встречи "):
        scope = "meetings"
        text = text.split(" ", 1)[1] if " " in text else ""
        lower = text.lower()

    # ═══ Извлекаем @username'ы ВМЕСТЕ с @ (стандарт проекта) ═══
    usernames = _USERNAME_RE.findall(text)  # ← ['@JDexMoment', '@user2']
    # Убираем @username'ы из текста для keyword-поиска
    clean_text = _USERNAME_RE.sub("", text).strip()

    keywords = [
        w for w in re.split(r"\s+", clean_text)
        if w and w.lower() not in _NOISE_WORDS and len(w) > 1
    ]

    return {
        "keywords": keywords,
        "usernames": usernames,  # ← С @
        "scope": scope,
    }


# ═══════════════════════════════════════════════════════════════
# Поиск задач
# ═══════════════════════════════════════════════════════════════
def _search_tasks_for_user(db_user, query: dict) -> List[Task]:
    qs = Task.objects.filter(
        assignees__user=db_user,
        status__in=["open", "done"],
    )

    if query["keywords"]:
        kw_q = Q()
        for kw in query["keywords"]:
            kw_q |= Q(title__icontains=kw) | Q(description__icontains=kw)
        qs = qs.filter(kw_q)

    if query["usernames"]:
        user_q = Q()
        for uname in query["usernames"]:
            # ═══ Убираем @ перед запросом к БД ═══
            clean = uname.lstrip("@").strip()
            user_q |= Q(assignees__user__username__iexact=clean)
            user_q |= Q(assignees__user__full_name__icontains=clean)
            user_q |= Q(creator__username__iexact=clean)
            user_q |= Q(creator__full_name__icontains=clean)
        qs = qs.filter(user_q)

    return list(
        qs.select_related("creator", "topic__chat")
          .prefetch_related("assignees__user", "subtasks__assignees")
          .order_by("due_date", "id")
          .distinct()[:MAX_RESULTS]
    )


def _search_tasks_in_chat(chat, topic, query: dict) -> List[Task]:
    """Поиск задач в указанном чате."""
    filters = {"topic__chat": chat, "status__in": ["open", "done"]}
    if topic is not None:
        filters["topic"] = topic

    qs = Task.objects.filter(**filters)

    if query["keywords"]:
        kw_q = Q()
        for kw in query["keywords"]:
            kw_q |= Q(title__icontains=kw) | Q(description__icontains=kw)
        qs = qs.filter(kw_q)

    if query["usernames"]:
        user_q = Q()
        for uname in query["usernames"]:
            # ═══ Убираем @ перед запросом к БД ═══
            clean = uname.lstrip("@").strip()
            user_q |= Q(assignees__user__username__iexact=clean)
            user_q |= Q(assignees__user__full_name__icontains=clean)
            user_q |= Q(creator__username__iexact=clean)
            user_q |= Q(creator__full_name__icontains=clean)
        qs = qs.filter(user_q)

    return list(
        qs.select_related("creator", "topic__chat")
          .prefetch_related("assignees__user", "subtasks__assignees")
          .order_by("due_date", "id")
          .distinct()[:MAX_RESULTS]
    )


# ═══════════════════════════════════════════════════════════════
# Поиск встреч
# ═══════════════════════════════════════════════════════════════
def _search_meetings_for_user(db_user, query: dict) -> List[Meeting]:
    """Поиск встреч, где пользователь — участник (для ЛС)."""
    qs = Meeting.objects.filter(
        participants=db_user,
        status="active",
    )

    if query["keywords"]:
        kw_q = Q()
        for kw in query["keywords"]:
            kw_q |= Q(title__icontains=kw) | Q(description__icontains=kw)
        qs = qs.filter(kw_q)

    if query["usernames"]:
        user_q = Q()
        for uname in query["usernames"]:
            clean = uname.lstrip("@").strip()  # ← убираем @
            user_q |= Q(participants__username__iexact=clean)
            user_q |= Q(participants__full_name__icontains=clean)
            user_q |= Q(creator__username__iexact=clean)
            user_q |= Q(creator__full_name__icontains=clean)
        qs = qs.filter(user_q)

    return list(
        qs.select_related("creator", "topic__chat", "recurrence")
          .prefetch_related("participants")
          .order_by("start_at", "id")
          .distinct()[:MAX_RESULTS]
    )


def _search_meetings_in_chat(chat, topic, query: dict) -> List[Meeting]:
    filters = {"topic__chat": chat, "status": "active"}
    if topic is not None:
        filters["topic"] = topic

    qs = Meeting.objects.filter(**filters)

    if query["keywords"]:
        kw_q = Q()
        for kw in query["keywords"]:
            kw_q |= Q(title__icontains=kw) | Q(description__icontains=kw)
        qs = qs.filter(kw_q)

    if query["usernames"]:
        user_q = Q()
        for uname in query["usernames"]:
            clean = uname.lstrip("@").strip()  # ← убираем @
            user_q |= Q(participants__username__iexact=clean)
            user_q |= Q(participants__full_name__icontains=clean)
            user_q |= Q(creator__username__iexact=clean)
            user_q |= Q(creator__full_name__icontains=clean)
        qs = qs.filter(user_q)

    return list(
        qs.select_related("creator", "topic__chat", "recurrence")
          .prefetch_related("participants")
          .order_by("start_at", "id")
          .distinct()[:MAX_RESULTS]
    )


# ═══════════════════════════════════════════════════════════════
# Форматирование ответа
# ═══════════════════════════════════════════════════════════════
def _build_search_header(query: dict, tasks: List[Task], meetings: List[Meeting]) -> str:
    parts = []
    if query["keywords"]:
        parts.append("«" + " ".join(query["keywords"]) + "»")
    if query["usernames"]:
        parts.append("@" + ", @".join(query["usernames"]))

    target = " и ".join(parts) if parts else "всё"

    t_cnt = len(tasks)
    m_cnt = len(meetings)
    found = []
    if t_cnt:
        w = "а" if t_cnt == 1 else ("и" if t_cnt < 5 else "")
        found.append(f"{t_cnt} задач{w}")
    if m_cnt:
        w = "а" if m_cnt == 1 else ("и" if m_cnt < 5 else "")
        found.append(f"{m_cnt} встреч{w}")

    if not found:
        return f"🔍 По запросу {target} ничего не найдено."
    return f"🔍 Найдено по запросу {target}: " + ", ".join(found)


# ═══════════════════════════════════════════════════════════════
# Основной обработчик
# ═══════════════════════════════════════════════════════════════
async def _respond_search(message: Message, chat, topic, db_user, query_text: str):
    """Отвечает результатами поиска."""
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    query = _parse_search_query(query_text)

    # Пустой запрос — подсказка
    if not query["keywords"] and not query["usernames"]:
        await message.answer(
            "🔍 <b>Поиск по задачам и встречам</b>\n\n"
            "Примеры:\n"
            "• <code>/search бюджет</code> — по названию\n"
            "• <code>/search @username</code> — по участнику\n"
            "• <code>/search tasks релиз</code> — только задачи\n"
            "• <code>/search meetings планёрка</code> — только встречи",
            parse_mode="HTML",
        )
        return

    # Поиск в зависимости от контекста
    is_private = message.chat.type == "private"

    tasks = []
    meetings = []

    if is_private:
        if query["scope"] in ("all", "tasks"):
            tasks = await sync_to_async(_search_tasks_for_user)(db_user, query)
        if query["scope"] in ("all", "meetings"):
            meetings = await sync_to_async(_search_meetings_for_user)(db_user, query)
    else:
        if not chat:
            await message.answer("Не удалось определить чат.")
            return
        if query["scope"] in ("all", "tasks"):
            tasks = await sync_to_async(_search_tasks_in_chat)(chat, topic, query)
        if query["scope"] in ("all", "meetings"):
            meetings = await sync_to_async(_search_meetings_in_chat)(chat, topic, query)

    header = _build_search_header(query, tasks, meetings)
    await message.answer(header, parse_mode="HTML")

    # Задачи
    if tasks:
        task_ids = [t.id for t in tasks]
        comment_counts = await _get_comment_counts(task_ids=task_ids)
        for t in tasks:
            has_rec = t.recurrence_group_id is not None
            c_count = comment_counts.get(t.id, 0)
            await message.answer(
                _build_task_text(t),
                parse_mode="HTML",
                reply_markup=task_keyboard(
                    t.id,
                    has_recurrence=has_rec,
                    comment_count=c_count,
                    has_subtasks=bool(t.subtasks.all()),
                ),
            )

    # Встречи
    if meetings:
        attendance_data = await _load_attendance_icons(meetings)
        meeting_ids = [m.id for m in meetings]
        m_comment_counts = await _get_comment_counts(meeting_ids=meeting_ids)
        for m in meetings:
            has_rec = _has_recurrence(m)
            icons = attendance_data.get(m.id)
            c_count = m_comment_counts.get(m.id, 0)
            await message.answer(
                _build_meeting_text(m, icons),
                parse_mode="HTML",
                reply_markup=meeting_keyboard(
                    m.id,
                    has_recurrence=has_rec,
                    comment_count=c_count,
                ),
            )


# ═══════════════════════════════════════════════════════════════
# Команда /search
# ═══════════════════════════════════════════════════════════════
@router.message(Command("search"))
async def cmd_search(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    # Убираем саму команду "/search" из текста
    text = message.text or ""
    query_text = text[len("/search"):].strip()
    # Убираем "search@BotName" если есть
    if " " not in query_text and "@" in query_text:
        query_text = query_text.split("@")[0]
    await _respond_search(message, chat, topic, db_user, query_text)