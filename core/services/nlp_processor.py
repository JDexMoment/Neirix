"""
Standalone NLP-процессор для Celery-воркеров.

ИСПРАВЛЕНИЯ:
  1. Добавлен message_thread_id во все bot.send_message — ответы уходят в нужный тред
  2. _show_tasks_list и _show_meetings_list теперь показывают реальные списки
     (через _respond_tasks / _respond_meetings с подменой message-заглушки)
  3. Краткий промпт = меньше токенов
"""
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Callable

from django.utils import timezone
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "utils" / "prompts"


def _load_json(filename: str):
    path = _PROMPTS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_NLP_RULES = _load_json("nlp_rules.json")


def _format_rules(rules_list: list) -> str:
    # Более краткий формат
    lines = ["ПРАВИЛА:"]
    for i, rule in enumerate(rules_list, 1):
        lines.append(f"{i}. {rule}")
    return "\n".join(lines)


def _format_response_schema(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2)


_GENERIC_TITLES = frozenset({
    "название для поиска", "название", "задача", "встреча",
    "эту задачу", "эту встречу", "последнюю", "её", "ее",
    "данную задачу", "данную встречу", "текущую задачу", "текущую встречу",
})


def _is_title_valid(title: str) -> bool:
    if not title:
        return False
    return title.strip().lower() not in _GENERIC_TITLES


def _extract_title_from_text(text: str, intent: str) -> str:
    if intent == "create_meeting":
        m = re.search(r'с\s+(\w+)\s+\d+', text)
        if m:
            return f"встреча с {m.group(1)}"
        m = re.search(r'встреча\s+с\s+(\w+)', text)
        if m:
            return f"встреча с {m.group(1)}"
        m = re.search(r'встреча[—\-]\s*(.+)', text)
        if m:
            return m.group(1).strip()
    return ""


def _extract_task_title_from_text(text: str) -> str:
    """Пытается извлечь название задачи из текста, если LLM не смог."""
    if not text:
        return ""
    t = text
    # Убираем префикс "назначь/создай/добавь/новую задачу"
    t = re.sub(r'^(?:назначь|создай|добавь|новую)\s+задач[уи]\s+', '', t)
    # Убираем "задачу/задачи" в начале (если не было префикса)
    t = re.sub(r'^задач[ауи]\s+', '', t)
    # Убираем "для @username"
    t = re.sub(r'для\s+@\w+\s*', '', t)
    # Убираем начальные разделители (тире, двоеточие, запятые, пробелы)
    t = re.sub(r'^[\s\-–—,:=]+', '', t)
    # Берём текст до "до" (дата) или до конца
    m = re.search(r'^(.+?)(?:\s+до\s||$)', t)
    if m:
        candidate = m.group(1).strip().rstrip('.,!?')
        if candidate and len(candidate) > 3:
            return candidate
    candidate = t.strip().rstrip('.,!?')
    if candidate and len(candidate) > 3:
        return candidate
    return ""


def _parse_assignees(raw: str) -> List[str]:
    if not raw:
        return []
    found = re.findall(r'@(\w+)', raw)
    if found:
        return [u.strip() for u in found if u.strip()]
    return [raw.strip()]


# ── Определение реального chat_id (для PM → linked group) ──

async def _resolve_chat_id(chat_id: int, user_telegram_id: int) -> int:
    """
    Для приватных сообщений — возвращает chat_id привязанной группы.
    Для групповых — возвращает оригинальный chat_id.
    """
    from core.models import UserRole
    role = await sync_to_async(
        lambda: UserRole.objects.filter(
            user__telegram_id=user_telegram_id
        ).select_related("chat").first()
    )()
    if role:
        return role.chat.chat_id
    return chat_id


# ── Хелпер для отправки в тред ──

async def _send(bot, chat_id: int, text: str, thread_id: Optional[int] = None, **kwargs):
    """Отправляет сообщение в чат, учитывая thread_id."""
    kwargs.pop("message_thread_id", None)
    await bot.send_message(chat_id, text, message_thread_id=thread_id or None, **kwargs)


# ══════════════════════════════════════════════════════════════════
#  Контекст
# ══════════════════════════════════════════════════════════════════

async def _get_context(chat_id: int, user_telegram_id: int, text: str):
    from core.models import TelegramUser, TelegramChat, Topic
    from bot.db_utils import get_or_create_user_sync, get_or_create_chat_sync

    db_user = await sync_to_async(get_or_create_user_sync)(
        telegram_id=user_telegram_id, username="", full_name="", is_bot=False,
    )
    chat = await sync_to_async(get_or_create_chat_sync)(
        chat_id=chat_id, title="", chat_type="private",
    )

    from core.models import UserRole
    linked_role = await sync_to_async(
        lambda: UserRole.objects.filter(user=db_user).select_related("chat").first()
    )()
    if linked_role:
        linked_chat = linked_role.chat
    else:
        linked_chat = chat

    topic, _ = await sync_to_async(Topic.objects.get_or_create)(
        chat=linked_chat, thread_id=0, defaults={"is_active": True},
    )
    return linked_chat, topic, db_user


# ══════════════════════════════════════════════════════════════════
#  Поиск
# ══════════════════════════════════════════════════════════════════

async def _find_tasks(chat_id: int, title: str, user_telegram_id: int = 0) -> list:
    from core.models import Task
    effective_chat_id = await _resolve_chat_id(chat_id, user_telegram_id) if user_telegram_id else chat_id
    tasks = await sync_to_async(
        lambda: list(Task.objects.filter(
            topic__chat__chat_id=effective_chat_id, status="open", title__icontains=title
        ).select_related("creator").prefetch_related("assignees__user").order_by("-id"))
    )()
    if tasks:
        return tasks
    words = [w.strip().lower() for w in title.split() if len(w.strip()) > 2]
    if words:
        all_tasks = await sync_to_async(
            lambda: list(Task.objects.filter(
                topic__chat__chat_id=effective_chat_id, status="open"
            ).select_related("creator").prefetch_related("assignees__user"))
        )()
        scored = [(sum(1 for w in words if w in t.title.lower()), t) for t in all_tasks if sum(1 for w in words if w in t.title.lower()) > 0]
        scored.sort(key=lambda x: -x[0])
        if scored:
            return [t for _, t in scored]
    return []


async def _find_meetings(chat_id: int, title: str, user_telegram_id: int = 0) -> list:
    from core.models import Meeting
    effective_chat_id = await _resolve_chat_id(chat_id, user_telegram_id) if user_telegram_id else chat_id
    meetings = await sync_to_async(
        lambda: list(Meeting.objects.filter(
            topic__chat__chat_id=effective_chat_id, status="active", title__icontains=title
        ).select_related("creator").prefetch_related("participants").order_by("-id"))
    )()
    if meetings:
        return meetings
    words = [w.strip().lower() for w in title.split() if len(w.strip()) > 2]
    if words:
        all_m = await sync_to_async(
            lambda: list(Meeting.objects.filter(
                topic__chat__chat_id=effective_chat_id, status="active"
            ).select_related("creator").prefetch_related("participants"))
        )()
        scored = [(sum(1 for w in words if w in m.title.lower()), m) for m in all_m if sum(1 for w in words if w in m.title.lower()) > 0]
        scored.sort(key=lambda x: -x[0])
        if scored:
            return [m for _, m in scored]
    return []


async def _find_last_task(chat_id: int, user_telegram_id: int = 0):
    from core.models import Task
    effective_chat_id = await _resolve_chat_id(chat_id, user_telegram_id) if user_telegram_id else chat_id
    return await sync_to_async(
        lambda: Task.objects.filter(topic__chat__chat_id=effective_chat_id, status="open")
        .select_related("creator").prefetch_related("assignees__user")
        .order_by("-id").first()
    )()


async def _find_last_meeting(chat_id: int, user_telegram_id: int = 0):
    from core.models import Meeting
    effective_chat_id = await _resolve_chat_id(chat_id, user_telegram_id) if user_telegram_id else chat_id
    return await sync_to_async(
        lambda: Meeting.objects.filter(topic__chat__chat_id=effective_chat_id, status="active")
        .select_related("creator").prefetch_related("participants")
        .order_by("-id").first()
    )()


async def _parse_nlp_date(message_text: str, nlp_result: dict):
    new_date_str = nlp_result.get("new_date", "")
    new_time_str = nlp_result.get("new_time", "")
    if new_date_str:
        try:
            base = datetime.strptime(new_date_str, "%Y-%m-%d")
            if new_time_str:
                dt = datetime.strptime(f"{new_date_str} {new_time_str}", "%Y-%m-%d %H:%M")
            else:
                dt = base.replace(hour=9, minute=0)
            current_tz = timezone.get_current_timezone()
            return timezone.make_aware(dt, current_tz)
        except ValueError:
            pass
    return None


# ══════════════════════════════════════════════════════════════════
#  detect_intent
# ══════════════════════════════════════════════════════════════════

async def detect_intent(text: str) -> Optional[dict]:
    from core.utils.llm_client import LLMClient
    llm = LLMClient()
    today = timezone.localtime(timezone.now())
    date_context = f"Сегодня: {today.strftime('%d.%m.%Y')} ({today.strftime('%A')})"
    rules_text = _format_rules(_NLP_RULES["rules"])
    schema_text = _format_response_schema(_NLP_RULES["response_format"])
    prompt = (
        f"{date_context}\n\n"
        f"{rules_text}\n\n"
        f"Ответ СТРОГО в JSON:\n"
        f"{schema_text}\n\n"
        f"Сообщение: \"{text}\""
    )
    messages = [
        {"role": "system", "content": _NLP_RULES["system_prompt"]},
        {"role": "user", "content": prompt},
    ]
    try:
        raw = await llm.chat_completion(messages=messages, temperature=0.1, max_tokens=200)
        cleaned = raw.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0]
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0]
        cleaned = cleaned.strip()
        result = json.loads(cleaned)
        intent = result.get("intent", "unknown")
        logger.info("NLP processor: intent=%s params=%s", intent, result)
        if intent == "unknown":
            return None
        return result
    except Exception as e:
        logger.error("NLP processor detect failed: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════
#  ГЛАВНАЯ ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════════

async def process_nlp_message_standalone(
    bot,
    chat_id: int,
    user_telegram_id: int,
    text: str,
    db_message_id: int,
    telegram_msg_id: int,
    message_thread_id: int = 0,
) -> bool:
    nlp_result = await detect_intent(text)
    if not nlp_result:
        return False

    intent = nlp_result.get("intent", "unknown")
    logger.info("NLP processor handling: intent=%s", intent)

    kwargs = {"thread_id": message_thread_id or None}
    user_kwargs = {**kwargs, "user_telegram_id": user_telegram_id}

    if intent == "show_tasks":
        title = nlp_result.get("title", "")
        if _is_title_valid(title):
            await _show_single_task(bot, chat_id, nlp_result, text, **user_kwargs)
        else:
            await _show_tasks_list(bot, chat_id, text, **user_kwargs)
        return True

    if intent == "show_meetings":
        title = nlp_result.get("title", "")
        if _is_title_valid(title):
            await _show_single_meeting(bot, chat_id, nlp_result, text, **user_kwargs)
        else:
            await _show_meetings_list(bot, chat_id, text, **user_kwargs)
        return True

    if intent == "create_task":
        await _handle_create_task(bot, chat_id, user_telegram_id, text, db_message_id, telegram_msg_id, nlp_result, **kwargs)
        return True

    if intent == "create_meeting":
        await _handle_create_meeting(bot, chat_id, user_telegram_id, text, db_message_id, telegram_msg_id, nlp_result, **kwargs)
        return True

    if intent == "edit_task" or intent == "reschedule_task":
        # reschedule_task — LLM-изобретение, это то же самое что edit_task для задач
        await _handle_edit_task(bot, chat_id, user_telegram_id, text, nlp_result, **kwargs)
        return True

    if intent == "edit_meeting":
        await _handle_edit_meeting(bot, chat_id, user_telegram_id, text, nlp_result, **kwargs)
        return True

    if intent == "reschedule_meeting":
        await _handle_reschedule_meeting(bot, chat_id, user_telegram_id, text, nlp_result, **kwargs)
        return True

    if intent == "cancel_meeting":
        await _handle_cancel_meeting(bot, chat_id, user_telegram_id, nlp_result, **kwargs)
        return True

    if intent == "show_summary":
        await _send(bot, chat_id, "ℹ️ Саммари пока доступно только через бота.", **kwargs)
        return True

    return False


# ══════════════════════════════════════════════════════════════════
#  Вспомогательные функции для форматирования (синхронные обёртки)
# ══════════════════════════════════════════════════════════════════

def _build_task_text(task) -> str:
    from bot.handlers.tasks import _build_task_text as _orig
    return _orig(task)

def _build_meeting_text(meeting) -> str:
    from bot.handlers.meetings import _build_meeting_text as _orig
    return _orig(meeting)

def _format_assignees(task) -> str:
    from bot.handlers.tasks import _format_assignees as _orig
    return _orig(task)

def _format_creator(creator) -> str:
    from bot.handlers.tasks import _format_creator as _orig
    return _orig(creator)

def _format_due_date(task) -> str:
    from bot.handlers.tasks import _format_due_date as _orig
    return _orig(task)

def _format_participants(meeting) -> str:
    from bot.handlers.meetings import _format_participants as _orig
    return _orig(meeting)

def _format_meeting_time(meeting) -> str:
    from bot.handlers.meetings import _format_meeting_time as _orig
    return _orig(meeting)

def _format_meeting_creator(meeting) -> str:
    from bot.handlers.meetings import _format_creator as _orig
    return _orig(meeting.creator)


# ══════════════════════════════════════════════════════════════════
#  ПОКАЗ СПИСКОВ (теперь с реальными данными)
# ══════════════════════════════════════════════════════════════════

async def _show_tasks_list(bot, chat_id: int, text: str, thread_id: Optional[int] = None,
                           user_telegram_id: int = 0):
    """Показывает список задач в чате."""
    from bot.handlers.tasks import _respond_tasks, _parse_task_filters
    msg = _make_stub_message(bot, chat_id, text, thread_id, user_telegram_id)
    chat, topic, db_user = await _get_context(chat_id, user_telegram_id, text)
    filters = _parse_task_filters(text, db_user) if db_user else {}
    await _respond_tasks(msg, chat, topic, db_user, filters)


async def _show_meetings_list(bot, chat_id: int, text: str, thread_id: Optional[int] = None,
                              user_telegram_id: int = 0):
    """Показывает список встреч в чате."""
    from bot.handlers.meetings import _respond_meetings, _parse_meeting_filters
    msg = _make_stub_message(bot, chat_id, text, thread_id, user_telegram_id)
    chat, topic, db_user = await _get_context(chat_id, user_telegram_id, text)
    m_filters = _parse_meeting_filters(text)
    await _respond_meetings(msg, chat, topic, db_user, m_filters)


def _make_stub_message(bot, chat_id: int, text: str, thread_id: Optional[int] = None,
                       user_telegram_id: int = 0):
    """
    Создаёт объект-заглушку, имитирующий aiogram.Message,
    чтобы можно было вызывать _respond_tasks / _respond_meetings,
    которые внутри используют message.answer().
    Мы подменяем message.answer на bot.send_message с thread_id.
    """
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = "private"
    msg.chat.is_forum = False
    msg.text = text
    msg.message_id = 0
    msg.bot = bot
    msg.from_user = MagicMock()
    msg.from_user.id = user_telegram_id
    msg.from_user.username = ""
    msg.from_user.full_name = ""
    msg.from_user.is_bot = False

    # Переопределяем message.answer → bot.send_message с thread_id
    async def fake_answer(text, **kwargs):
        kwargs.pop("message_thread_id", None)
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        await bot.send_message(chat_id, text, **kwargs)

    msg.answer = fake_answer
    msg.reply = fake_answer
    return msg


# ══════════════════════════════════════════════════════════════════
#  ОТВЕТ НА ВОПРОС О КОНКРЕТНОМ ЭЛЕМЕНТЕ
# ══════════════════════════════════════════════════════════════════

async def _show_single_task(bot, chat_id: int, nlp_result: dict, text: str, thread_id: Optional[int] = None, user_telegram_id: int = 0):
    from bot.keyboards.inline import task_keyboard as _task_kb
    tasks = await _find_tasks(chat_id, nlp_result.get("title", ""), user_telegram_id)
    if not tasks:
        last = await _find_last_task(chat_id, user_telegram_id)
        if not last:
            await _send(bot, chat_id, "У вас нет активных задач.", thread_id=thread_id)
            return
        tasks = [last]

    task = tasks[0]
    question_lower = text.lower()
    answer_parts = []

    if any(w in question_lower for w in ["как называ", "какое название", "имя", "заголовок"]):
        answer_parts.append(f"📋 Задача называется: <b>{task.title}</b>")
    if any(w in question_lower for w in ["когда", "какого числа", "дата", "срок", "дедлайн"]):
        dt = task.due_date
        if dt:
            if timezone.is_aware(dt):
                dt = timezone.localtime(dt)
            answer_parts.append(f"📅 Срок задачи «{task.title}» — {dt.strftime('%d.%m.%Y')}")
        else:
            answer_parts.append(f"📅 У задачи «{task.title}» нет срока.")
    if any(w in question_lower for w in ["кто", "ответствен", "исполнитель", "назначен"]):
        answer_parts.append(f"👤 Исполнитель задачи «{task.title}»: {_format_assignees(task)}")
    if any(w in question_lower for w in ["кто создал", "кто назначил", "автор"]):
        answer_parts.append(f"📝 Задачу «{task.title}» назначил(а): {_format_creator(task.creator)}")

    if answer_parts:
        await _send(bot, chat_id, "\n".join(answer_parts), parse_mode="HTML", thread_id=thread_id)
    has_rec = task.is_template and task.recurrence_group_id is not None
    markup = _task_kb(task.id, has_recurrence=has_rec)
    await _send(bot, chat_id, _build_task_text(task), parse_mode="HTML",
                reply_markup=markup, thread_id=thread_id)


async def _show_single_meeting(bot, chat_id: int, nlp_result: dict, text: str, thread_id: Optional[int] = None, user_telegram_id: int = 0):
    from bot.keyboards.inline import meeting_keyboard as _meeting_kb
    meetings = await _find_meetings(chat_id, nlp_result.get("title", ""), user_telegram_id)
    if not meetings:
        last = await _find_last_meeting(chat_id, user_telegram_id)
        if not last:
            await _send(bot, chat_id, "У вас нет активных встреч.", thread_id=thread_id)
            return
        meetings = [last]

    meeting = meetings[0]
    question_lower = text.lower()
    answer_parts = []

    if any(w in question_lower for w in ["как называ", "какое название", "имя"]):
        answer_parts.append(f"📋 Встреча называется: <b>{meeting.title}</b>")
    if any(w in question_lower for w in ["когда", "какого числа", "дата", "во сколько", "время"]):
        answer_parts.append(f"📅 Встреча «{meeting.title}» — {_format_meeting_time(meeting)}")
    if any(w in question_lower for w in ["кто", "участник", "с кем"]):
        answer_parts.append(f"👥 Участники встречи «{meeting.title}»: {_format_participants(meeting)}")
    if any(w in question_lower for w in ["кто создал", "кто назначил", "автор"]):
        answer_parts.append(f"📝 Встречу назначил(а): {_format_meeting_creator(meeting)}")

    if answer_parts:
        await _send(bot, chat_id, "\n".join(answer_parts), parse_mode="HTML", thread_id=thread_id)
    has_rec = bool(getattr(meeting, 'recurrence_id', None))
    markup = _meeting_kb(meeting.id, has_recurrence=has_rec)
    await _send(bot, chat_id, _build_meeting_text(meeting), parse_mode="HTML",
                reply_markup=markup, thread_id=thread_id)


# ══════════════════════════════════════════════════════════════════
#  СОЗДАНИЕ ЗАДАЧИ
# ══════════════════════════════════════════════════════════════════

async def _handle_create_task(bot, chat_id: int, user_telegram_id: int, text: str,
                               db_message_id: int, telegram_msg_id: int,
                               nlp_result: dict, thread_id: Optional[int] = None):
    from core.models import Message as DBMessage, Topic
    from core.services.task_service import TaskService

    title = nlp_result.get("title", "")
    if not title:
        # ═══ Пробуем извлечь название из текста ═══
        title = _extract_task_title_from_text(text)
    if not title:
        await _send(bot, chat_id, "Не удалось определить название задачи.", thread_id=thread_id)
        return

    username = nlp_result.get("new_assignee") or nlp_result.get("username", "")
    due_date_str = nlp_result.get("new_date", nlp_result.get("date", ""))

    chat, topic, db_user = await _get_context(chat_id, user_telegram_id, text)
    if not chat:
        await _send(bot, chat_id, "Не удалось определить чат.", thread_id=thread_id)
        return

    source_msg = await sync_to_async(DBMessage.objects.get)(id=db_message_id)

    assignees = []
    if username:
        assignees = [f"@{u}" for u in _parse_assignees(username)]

    task_data = {
        "title": title,
        "assignees": assignees,
        "due_date": due_date_str if due_date_str else None,
        "description": "",
    }

    ts = TaskService()
    task = await ts._create_task_from_data(task_data, source_msg)
    if not task:
        await _send(bot, chat_id, "Не удалось создать задачу.", thread_id=thread_id)
        return

    assignee_exists = await sync_to_async(lambda: task.assignees.exists())()
    if assignee_exists:
        # ═══ Fix: select_related('user') для TaskAssignee ═══
        a_qs = await sync_to_async(
            lambda: list(task.assignees.select_related("user").all())
        )()
        names = []
        for a in a_qs:
            user = a.user  # уже загружен select_related
            if user.username:
                names.append(f"@{user.username}")
            elif user.full_name:
                names.append(user.full_name)
            else:
                names.append(f"id={user.id}")
        assignee_str = ", ".join(names)
    else:
        assignee_str = "не назначен"

    due_str = task.due_date.strftime("%d.%m.%Y") if task.due_date else "без срока"
    await _send(bot, chat_id,
        f"✅ <b>{task.title}</b>\n👤 {assignee_str}\n📅 до {due_str}",
        parse_mode="HTML", thread_id=thread_id,
    )


# ══════════════════════════════════════════════════════════════════
#  СОЗДАНИЕ ВСТРЕЧИ
# ══════════════════════════════════════════════════════════════════

async def _handle_create_meeting(bot, chat_id: int, user_telegram_id: int, text: str,
                                  db_message_id: int, telegram_msg_id: int,
                                  nlp_result: dict, thread_id: Optional[int] = None):
    from core.models import Message as DBMessage
    from core.services.meeting_service import MeetingService

    title = nlp_result.get("title", "")
    if not title:
        title = _extract_title_from_text(text, "create_meeting")
    if not title:
        await _send(bot, chat_id, "Не удалось определить название встречи.", thread_id=thread_id)
        return

    username = nlp_result.get("new_assignee") or nlp_result.get("username", "")
    date_str = nlp_result.get("new_date", nlp_result.get("date", ""))
    time_str = nlp_result.get("new_time", "09:00")

    if not date_str:
        await _send(bot, chat_id, "Не удалось определить дату встречи.", thread_id=thread_id)
        return

    chat, topic, db_user = await _get_context(chat_id, user_telegram_id, text)
    if not chat:
        await _send(bot, chat_id, "Не удалось определить чат.", thread_id=thread_id)
        return

    source_msg = await sync_to_async(DBMessage.objects.get)(id=db_message_id)

    participants = []
    if username:
        participants = [f"@{u}" for u in _parse_assignees(username)]

    meeting_data = {
        "title": title,
        "participants": participants,
        "start_at": f"{date_str}T{time_str}",
        "description": "",
    }

    ms = MeetingService()
    meeting = await ms._create_meeting_from_data(meeting_data, source_msg)
    if not meeting:
        await _send(bot, chat_id, "Не удалось создать встречу.", thread_id=thread_id)
        return

    if meeting.is_all_hands:
        p_str = "Все участники"
    else:
        part_exists = await sync_to_async(lambda: meeting.participants.exists())()
        if part_exists:
            p_qs = await sync_to_async(lambda: list(meeting.participants.all()))()
            names = [f"@{p.username}" if p.username else (p.full_name or f"id={p.id}") for p in p_qs]
            p_str = ", ".join(names)
        else:
            p_str = "не определены"

    await _send(bot, chat_id,
        f"✅ <b>{meeting.title}</b>\n  ⏰ {meeting.start_at.strftime('%d.%m.%Y %H:%M')}\n  👥 {p_str}",
        parse_mode="HTML", thread_id=thread_id,
    )
    if username:
        from celery_app.tasks.send_reminders import send_meeting_assigned_notification
        send_meeting_assigned_notification.delay(meeting.id)


# ══════════════════════════════════════════════════════════════════
#  РЕДАКТИРОВАНИЕ ЗАДАЧИ
# ══════════════════════════════════════════════════════════════════

async def _handle_edit_task(bot, chat_id: int, user_telegram_id: int, text: str, nlp_result: dict,
                             thread_id: Optional[int] = None):
    from core.services.task_service import TaskService

    title = nlp_result.get("title", "")
    new_title_val = nlp_result.get("new_title", "")
    new_assignee_raw = nlp_result.get("new_assignee", "")
    new_date_str = nlp_result.get("new_date", "")

    if not _is_title_valid(title):
        last = await _find_last_task(chat_id, user_telegram_id)
        if not last:
            await _send(bot, chat_id, "Укажите название задачи для редактирования.", thread_id=thread_id)
            return
        tasks = [last]
    else:
        tasks = await _find_tasks(chat_id, title, user_telegram_id)

    if not tasks:
        await _send(bot, chat_id, "Не найдена активная задача.", thread_id=thread_id)
        return

    task = tasks[0]
    ts = TaskService()
    changes = []

    if new_title_val:
        old_title = task.title
        ok = await ts.update_title(task.id, new_title_val)
        if ok:
            changes.append(f"✏️ Название: {old_title} → {new_title_val}")
            task = await ts.get_task_by_id(task.id)

    if new_date_str:
        old_due = _format_due_date(task)
        ok = await ts.update_due_date(task.id, new_date_str)
        if ok:
            task = await ts.get_task_by_id(task.id)
            new_due = _format_due_date(task)
            if old_due != new_due:
                changes.append(f"📅 Срок: {old_due} → {new_due}")

    # ═══ Fix: если new_assignee пустой, но есть username — используем username ═══
    if not new_assignee_raw and nlp_result.get("username"):
        new_assignee_raw = nlp_result["username"]
    
    if new_assignee_raw:
        assignees = _parse_assignees(new_assignee_raw)
        text_lower = text.lower()
        is_add_task = any(w in text_lower for w in ["добавь", "добавить", "добав", "ещё"])
        is_remove_task = any(w in text_lower for w in ["удали", "убрать", "убери", "исключи"])
        if is_add_task:
            # Добавляем к существующим
            existing_assignees = await sync_to_async(
                lambda: list(task.assignees.select_related("user").all())
            )()
            existing_names = set()
            for a in existing_assignees:
                if a.user.username:
                    existing_names.add(a.user.username)
            for u in assignees:
                existing_names.add(u)
            final_names = [f"@{u}" for u in existing_names]
        elif is_remove_task:
            existing_assignees = await sync_to_async(
                lambda: list(task.assignees.select_related("user").all())
            )()
            keep = [a for a in existing_assignees if a.user.username not in assignees]
            final_names = [f"@{a.user.username}" for a in keep if a.user.username] if keep else ["@none"]
        else:
            final_names = [f"@{u}" for u in assignees]
        old_assign = _format_assignees(task)
        ok = await ts.update_assignees(task.id, final_names)
        if ok:
            task = await ts.get_task_by_id(task.id)
            new_assign = _format_assignees(task)
            if old_assign != new_assign:
                changes.append(f"👤 Исполнитель: {old_assign} → {new_assign}")

    if changes:
        task = await ts.get_task_by_id(task.id)
        await _send(bot, chat_id, f"✅ Задача обновлена\n" + "\n".join(changes),
                    parse_mode="HTML", thread_id=thread_id)
    else:
        await _send(bot, chat_id, "Нечего изменять.", thread_id=thread_id)


# ══════════════════════════════════════════════════════════════════
#  РЕДАКТИРОВАНИЕ ВСТРЕЧИ
# ══════════════════════════════════════════════════════════════════

async def _handle_edit_meeting(bot, chat_id: int, user_telegram_id: int, text: str, nlp_result: dict,
                                thread_id: Optional[int] = None):
    from core.services.meeting_service import MeetingService
    from bot.handlers.meetings import _notify_meeting_changed

    title = nlp_result.get("title", "")
    new_title_val = nlp_result.get("new_title", "")
    new_participant_raw = nlp_result.get("new_assignee", "")
    new_date_str = nlp_result.get("new_date", "")
    new_time_str = nlp_result.get("new_time", "")

    if not _is_title_valid(title):
        last = await _find_last_meeting(chat_id, user_telegram_id)
        if not last:
            await _send(bot, chat_id, "Укажите название встречи для редактирования.", thread_id=thread_id)
            return
        meetings = [last]
    else:
        meetings = await _find_meetings(chat_id, title, user_telegram_id)

    if not meetings:
        await _send(bot, chat_id, "Не найдена активная встреча.", thread_id=thread_id)
        return

    meeting = meetings[0]
    ms = MeetingService()
    changes = []

    if new_title_val:
        old_title = meeting.title
        ok = await ms.update_title(meeting.id, new_title_val)
        if ok:
            changes.append(f"✏️ Название: {old_title} → {new_title_val}")
            meeting = await ms.get_meeting_by_id(meeting.id)

    if new_date_str:
        # Проверяем, что пользователь ДЕЙСТВИТЕЛЬНО просил изменить дату/время
        text_lower_edit = text.lower()
        user_wants_date_change = any(w in text_lower_edit for w in [
            "перенеси", "срок", "дата", "время", "число",
            "день", "час", "минут", "позже", "раньше",
        ])
        # Если в nlp_result есть new_date, но пользователь в тексте просил ТОЛЬКО название — игнорируем
        has_only_title_change = new_title_val and not user_wants_date_change

        if not has_only_title_change:
            old_time = _format_meeting_time(meeting)
            use_time = new_time_str if new_time_str else meeting.start_at.strftime("%H:%M")
            new_dt = await _parse_nlp_date(text, {"new_date": new_date_str, "new_time": use_time})
            if new_dt and new_dt > timezone.now():
                updated = await ms.reschedule_meeting(meeting.id, new_dt)
                if updated:
                    meeting = await ms.get_meeting_by_id(meeting.id)
                    changes.append(f"⏰ Время: {old_time} → {new_dt.strftime('%d.%m.%Y %H:%M')}")

    # ═══ Fix: если new_assignee пустой, но есть username — используем username ═══
    if not new_participant_raw and nlp_result.get("username"):
        new_participant_raw = nlp_result["username"]
    
    if new_participant_raw:
        participants = _parse_assignees(new_participant_raw)
        old_part = _format_participants(meeting)
        text_lower = text.lower()
        is_add = any(w in text_lower for w in ["добавь", "добавить", "добав", "ещё"])
        is_remove = any(w in text_lower for w in ["удали", "убрать", "убери", "исключи", "remove", "delete"])
        if is_remove:
            # Удаляем указанных участников из существующих
            existing = await sync_to_async(lambda: list(meeting.participants.all()))()
            keep = [p for p in existing if p.username not in participants]
            keep_usernames = [f"@{p.username}" for p in keep if p.username]
            final_usernames = keep_usernames if keep_usernames else ["@none"]
        elif is_add:
            existing = await sync_to_async(lambda: list(meeting.participants.all()))()
            all_usernames = {p.username for p in existing if p.username}
            all_usernames.update(participants)
            final_usernames = [f"@{u}" for u in all_usernames]
        else:
            final_usernames = [f"@{u}" for u in participants]
        ok = await ms.update_participants(meeting.id, final_usernames)
        if ok:
            meeting = await ms.get_meeting_by_id(meeting.id)
            new_part = _format_participants(meeting)
            if old_part != new_part:
                changes.append(f"👥 Участники: {old_part} → {new_part}")

    if changes:
        meeting = await ms.get_meeting_by_id(meeting.id)
        await _send(bot, chat_id, f"✅ Встреча обновлена\n" + "\n".join(changes),
                    parse_mode="HTML", thread_id=thread_id)
        _notify_meeting_changed(meeting, "\n".join(changes))
    else:
        await _send(bot, chat_id, "Нечего изменять.", thread_id=thread_id)


# ══════════════════════════════════════════════════════════════════
#  ПЕРЕНОС ВСТРЕЧИ
# ══════════════════════════════════════════════════════════════════

async def _handle_reschedule_meeting(bot, chat_id: int, user_telegram_id: int, text: str, nlp_result: dict,
                                      thread_id: Optional[int] = None):
    from core.services.meeting_service import MeetingService
    from bot.handlers.meetings import _notify_meeting_changed

    ms = MeetingService()
    title = nlp_result.get("title", "")

    if not title or not _is_title_valid(title):
        parts = re.split(r'\s+на\s+', text, maxsplit=1)
        if len(parts) > 1:
            title = parts[0].replace("перенеси", "").replace("передвинь", "").strip()
        else:
            last = await _find_last_meeting(chat_id, user_telegram_id)
            if last:
                title = last.title

    if not title:
        await _send(bot, chat_id, "Укажите название встречи для переноса.", thread_id=thread_id)
        return

    meetings = await _find_meetings(chat_id, title, user_telegram_id)
    if not meetings:
        last = await _find_last_meeting(chat_id, user_telegram_id)
        if last:
            meetings = [last]
        else:
            await _send(bot, chat_id, "Не найдена активная встреча.", thread_id=thread_id)
            return

    meeting = meetings[0]
    # ═══ Если в тексте есть слова относительного сдвига — игнорируем new_date от LLM ═══
    text_lower_r = text.lower()
    if any(w in text_lower_r for w in ["раньше", "позже", "часов", "часа", "минут", "дней", "дня", "день", "недел"]):
        # Убираем new_date, чтобы не мешал regex-обработчикам ниже
        nlp_result.pop("new_date", None)
        nlp_result.pop("new_time", None)
        new_start_at = None
    else:
        # Если указана только дата (без времени) — сохраняем исходное время встречи
        if nlp_result.get("new_date") and not nlp_result.get("new_time"):
            orig_time = timezone.localtime(meeting.start_at).strftime("%H:%M")
            nlp_result["new_time"] = orig_time
        new_start_at = await _parse_nlp_date(text, nlp_result)

    if not new_start_at:
        # Сдвиг на N часов (раньше/позже)
        m_earlier = re.search(r'на\s+(\d+)\s+час[а]?\s+раньше', text)
        m_later = re.search(r'на\s+(\d+)\s+час[а]?\s+позже', text)
        if m_earlier:
            hours = int(m_earlier.group(1))
            new_start_at = meeting.start_at - timedelta(hours=hours)
        elif m_later:
            hours = int(m_later.group(1))
            new_start_at = meeting.start_at + timedelta(hours=hours)
        # Сдвиг на N часов/минут
        if not new_start_at:
            m = re.search(r'на\s+(\d+)\s+час', text)
            if m:
                hours = int(m.group(1))
                new_start_at = meeting.start_at + timedelta(hours=hours)
        if not new_start_at:
            m = re.search(r'на\s+(\d+)\s+мин', text)
            if m:
                mins = int(m.group(1))
                new_start_at = meeting.start_at + timedelta(minutes=mins)
        if not new_start_at:
            m = re.search(r'на\s+недел[юя]', text)
            if m:
                new_start_at = meeting.start_at + timedelta(weeks=1)
        if not new_start_at:
            m_earlier_day = re.search(r'на\s+(\d+)?\s*д(?:ень|ня|ней|ни)?\s+раньше', text)
            if m_earlier_day:
                days = int(m_earlier_day.group(1)) if m_earlier_day.group(1) else 1
                new_start_at = meeting.start_at - timedelta(days=days)
        if not new_start_at:
            m_later_day = re.search(r'на\s+(\d+)?\s*д(?:ень|ня|ней|ни)?\s+позже', text)
            if m_later_day:
                days = int(m_later_day.group(1)) if m_later_day.group(1) else 1
                new_start_at = meeting.start_at + timedelta(days=days)
        if not new_start_at:
            # Если указано только время (new_time) без даты — меняем время
            if nlp_result.get("new_time") and not nlp_result.get("new_date"):
                try:
                    tp = nlp_result["new_time"].split(":")
                    h, mt = int(tp[0]), int(tp[1]) if len(tp) > 1 else 0
                    ld = timezone.localtime(meeting.start_at)
                    ld = ld.replace(hour=h, minute=mt, second=0, microsecond=0)
                    # ld уже aware (от localtime), make_aware не нужен
                    new_start_at = ld
                except (ValueError, IndexError):
                    pass
        if not new_start_at:
            m = re.search(r'на\s+(\d+)\s+дн', text)
            if m:
                days = int(m.group(1))
                new_start_at = meeting.start_at + timedelta(days=days)
            else:
                m = re.search(r'на\s+(\d{1,2})\s+число', text)
            if m:
                day = int(m.group(1))
                now = timezone.now()
                try:
                    new_dt = now.replace(day=day, hour=meeting.start_at.hour,
                                         minute=meeting.start_at.minute,
                                         second=0, microsecond=0)
                    if new_dt <= now:
                        if now.month == 12:
                            new_dt = new_dt.replace(year=now.year + 1, month=1)
                        else:
                            new_dt = new_dt.replace(month=now.month + 1)
                    new_start_at = new_dt
                except ValueError:
                    pass

    if not new_start_at:
        await _send(bot, chat_id, "Укажите новую дату для переноса.", thread_id=thread_id)
        return
    if new_start_at <= timezone.now():
        await _send(bot, chat_id, "Новая дата должна быть в будущем.", thread_id=thread_id)
        return

    old_time = timezone.localtime(meeting.start_at).strftime("%d.%m.%Y %H:%M")
    updated = await ms.reschedule_meeting(meeting.id, new_start_at)
    if updated:
        new_time = timezone.localtime(new_start_at).strftime("%d.%m.%Y %H:%M")
        await _send(bot, chat_id,
            f"✅ Встреча «{meeting.title}» перенесена:\n{old_time} → {new_time}",
            thread_id=thread_id)
        _notify_meeting_changed(updated, f"⏰ Время: {old_time} → {new_time}")
    else:
        await _send(bot, chat_id, "Не удалось перенести встречу.", thread_id=thread_id)


# ══════════════════════════════════════════════════════════════════
#  ОТМЕНА ВСТРЕЧИ
# ══════════════════════════════════════════════════════════════════

async def _handle_cancel_meeting(bot, chat_id: int, user_telegram_id: int, nlp_result: dict,
                                  thread_id: Optional[int] = None):
    from core.services.meeting_service import MeetingService
    ms = MeetingService()
    title = nlp_result.get("title", "")

    if not title:
        last = await _find_last_meeting(chat_id, user_telegram_id)
        if last:
            title = last.title
        else:
            await _send(bot, chat_id, "Укажите название встречи для отмены.", thread_id=thread_id)
            return

    meetings = await _find_meetings(chat_id, title, user_telegram_id)
    if not meetings:
        await _send(bot, chat_id, "Не найдена активная встреча.", thread_id=thread_id)
        return

    meeting = meetings[0]
    success = await ms.cancel_meeting(meeting.id)
    if success:
        await _send(bot, chat_id, f"✅ Встреча «{meeting.title}» отменена.", thread_id=thread_id)
        from celery_app.tasks.send_reminders import send_meeting_cancelled_notification
        send_meeting_cancelled_notification.delay(meeting.id)
    else:
        await _send(bot, chat_id, "Не удалось отменить встречу.", thread_id=thread_id)
