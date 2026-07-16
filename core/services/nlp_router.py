"""
NLP-роутер: определяет намерение пользователя через GigaChat
и выполняет соответствующее действие.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

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
    lines = ["ПРАВИЛА:"]
    for i, rule in enumerate(rules_list, 1):
        lines.append(f"{i}. {rule}")
    return "\n".join(lines)


def _format_response_schema(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2)


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
        raw = await llm.chat_completion(messages=messages, temperature=0.1, max_tokens=150)
        cleaned = raw.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0]
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0]
        cleaned = cleaned.strip()
        result = json.loads(cleaned)
        intent = result.get("intent", "unknown")
        logger.info("NLP router: intent=%s params=%s", intent, result)
        if intent == "unknown":
            return None
        return result
    except Exception as e:
        logger.error("NLP router detect failed: %s", e)
        return None


async def handle_nlp_command(message, nlp_result: dict) -> bool:
    """
    Всегда возвращает True, если интент распознан (даже при ошибке).
    """
    from bot.handlers.tasks import _handle_nlp_query as task_nlp
    from bot.handlers.meetings import _handle_nlp_meeting_query as meeting_nlp

    intent = nlp_result.get("intent", "unknown")
    nlp_filter = nlp_result.get("filter", "all")
    filters = {}
    if nlp_filter in ("today", "tomorrow", "overdue", "week", "done"):
        filters[nlp_filter] = True
    elif nlp_filter == "all":
        filters["list_all"] = True
    if nlp_result.get("username"):
        filters["username"] = nlp_result["username"].lstrip("@")

    try:
        if intent == "show_tasks":
            await task_nlp(message, intent, filters)
        elif intent == "show_meetings":
            await meeting_nlp(message, intent, filters)
        elif intent == "show_summary":
            await _handle_summary(message, nlp_result)
        elif intent == "reschedule_meeting":
            await _handle_reschedule_meeting(message, nlp_result)
        elif intent == "cancel_meeting":
            await _handle_cancel_meeting(message, nlp_result)
        elif intent == "create_task":
            await _handle_create_task(message, nlp_result)
        elif intent == "create_meeting":
            await _handle_create_meeting(message, nlp_result)
        elif intent == "edit_task":
            await _handle_edit_task(message, nlp_result)
        elif intent == "edit_meeting":
            await _handle_edit_meeting(message, nlp_result)
        else:
            return False
        return True
    except Exception as e:
        logger.error("NLP handler error: %s", e, exc_info=True)
        try:
            await message.answer("⚠️ Произошла ошибка при обработке запроса.")
        except Exception:
            pass
        return True  # всё равно возвращаем True — сообщение обработано


async def _get_ctx(message):
    from bot.utils import get_chat_context
    return await get_chat_context(message)


async def _find_tasks(message, title: str):
    """Ищет задачи по названию с гибким поиском."""
    from core.models import Task
    chat, topic, db_user = await _get_ctx(message)
    if not chat:
        return []
    
    # 1. Точный icontains
    tasks = await sync_to_async(
        lambda: list(Task.objects.filter(topic__chat=chat, status="open", title__icontains=title)
                     .prefetch_related("assignees__user").order_by("-id"))
    )()
    if tasks:
        return tasks
    
    # 2. Разбиваем на слова и ищем по каждому
    words = [w.strip().lower() for w in title.split() if len(w.strip()) > 2]
    if words:
        q = {"topic__chat": chat, "status": "open"}
        qs = Task.objects.filter(**q).prefetch_related("assignees__user")
        all_tasks = await sync_to_async(list)(qs)
        scored = []
        for t in all_tasks:
            score = sum(1 for w in words if w in t.title.lower())
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: -x[0])
        if scored:
            return [t for _, t in scored]
    
    return []


async def _find_meetings(message, title: str):
    """Ищет встречи по названию с гибким поиском."""
    from core.models import Meeting
    chat, topic, db_user = await _get_ctx(message)
    if not chat:
        return []
    
    meetings = await sync_to_async(
        lambda: list(Meeting.objects.filter(topic__chat=chat, status="active", title__icontains=title)
                     .prefetch_related("participants").order_by("-id"))
    )()
    if meetings:
        return meetings
    
    words = [w.strip().lower() for w in title.split() if len(w.strip()) > 2]
    if words:
        qs = Meeting.objects.filter(topic__chat=chat, status="active").prefetch_related("participants")
        all_m = await sync_to_async(list)(qs)
        scored = []
        for m in all_m:
            score = sum(1 for w in words if w in m.title.lower())
            if score > 0:
                scored.append((score, m))
        scored.sort(key=lambda x: -x[0])
        if scored:
            return [m for _, m in scored]
    
    return []


async def _parse_nlp_date(message, nlp_result: dict) -> Optional[datetime]:
    """Извлекает дату из NLP-результата или текста сообщения."""
    new_date_str = nlp_result.get("new_date", "")
    new_time_str = nlp_result.get("new_time", "")
    
    # Пробуем распарсить дату
    if new_date_str:
        try:
            base = datetime.strptime(new_date_str, "%Y-%m-%d")
            if new_time_str:
                try:
                    dt = datetime.strptime(f"{new_date_str} {new_time_str}", "%Y-%m-%d %H:%M")
                except ValueError:
                    dt = base.replace(hour=int(new_time_str.split(":")[0]), minute=int(new_time_str.split(":")[1]) if ":" in new_time_str else 0)
            else:
                dt = base.replace(hour=9, minute=0)
            current_tz = timezone.get_current_timezone()
            return timezone.make_aware(dt, current_tz)
        except ValueError:
            pass
    
    return None


# ══════════════════════════════════════════════════════════════════
#  ПОКАЗ СПИСКОВ
# ══════════════════════════════════════════════════════════════════


async def _handle_summary(message, nlp_result: dict) -> None:
    from bot.handlers.summary import send_summary_response
    from core.services.summary_service import SummaryService
    from core.models import Topic, Summary

    date_str = nlp_result.get("date", "")
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if date_str:
        period_start = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
        current_tz = timezone.get_current_timezone()
        period_start = timezone.make_aware(period_start, current_tz)
        period_end = period_start + timedelta(days=1)
    elif "вчера" in (message.text or "").lower() or "yesterday" in (message.text or "").lower():
        period_start = today_start - timedelta(days=1)
        period_end = today_start
    else:
        period_start = today_start
        period_end = period_start + timedelta(days=1)

    chat, topic, db_user = await _get_ctx(message)
    if not chat:
        await message.answer("Не удалось определить чат.")
        return

    target_topic = topic or (await sync_to_async(
        lambda: Topic.objects.get_or_create(chat=chat, thread_id=0, defaults={"is_active": True})
    )())[0]

    existing = await sync_to_async(
        lambda: Summary.objects.filter(topic=target_topic, period_start=period_start, period_end=period_end).first()
    )()
    if existing:
        await send_summary_response(message, existing)
        return

    await message.answer("⏳ Генерирую саммари...")
    ss = SummaryService()
    summary = await ss.generate_summary_for_period(target_topic, period_start, period_end)
    if summary:
        await send_summary_response(message, summary)
    else:
        await message.answer("📭 Нет сообщений за этот период.")


# ══════════════════════════════════════════════════════════════════
#  ПЕРЕНОС ВСТРЕЧИ
# ══════════════════════════════════════════════════════════════════


async def _handle_reschedule_meeting(message, nlp_result: dict) -> None:
    from core.services.meeting_service import MeetingService
    from bot.handlers.meetings import _notify_meeting_changed

    ms = MeetingService()
    title = nlp_result.get("title", "")
    
    if not title:
        # Пробуем извлечь из текста: "перенеси X на Y"
        text = message.text or ""
        # Ищем название до "на"
        parts = re.split(r'\s+на\s+', text, maxsplit=1)
        if len(parts) > 1:
            title = parts[0].replace("перенеси", "").replace("передвинь", "").strip()
        else:
            # Последняя созданная встреча
            meetings = await _find_meetings(message, "")
            if meetings:
                title = meetings[0].title

    if not title:
        await message.answer("Укажите название встречи для переноса.")
        return

    meetings = await _find_meetings(message, title)
    if not meetings:
        await message.answer(f"Не найдена активная встреча «{title}».")
        return

    meeting = meetings[0]
    
    # Парсим новую дату
    new_start_at = await _parse_nlp_date(message, nlp_result)
    if not new_start_at:
        # Пробуем распарсить из текста "на 18 число" или "на 2 дня"
        text = message.text or ""
        # "на N число" → дата N текущего месяца
        m = re.search(r'на\s+(\d{1,2})\s+число', text)
        if m:
            day = int(m.group(1))
            now = timezone.now()
            try:
                new_dt = now.replace(day=day, hour=9, minute=0, second=0, microsecond=0)
                if new_dt <= now:
                    # следующий месяц
                    if now.month == 12:
                        new_dt = new_dt.replace(year=now.year+1, month=1)
                    else:
                        new_dt = new_dt.replace(month=now.month+1)
                new_start_at = new_dt
            except ValueError:
                pass
        
        if not new_start_at:
            # "на N дня/дней" → сдвиг от текущей даты встречи
            m = re.search(r'на\s+(\d+)\s+дн', text)
            if m:
                days = int(m.group(1))
                new_start_at = meeting.start_at + timedelta(days=days)

    if not new_start_at:
        await message.answer("Укажите новую дату для переноса.")
        return

    if new_start_at <= timezone.now():
        await message.answer("Новая дата должна быть в будущем.")
        return

    old_time = meeting.start_at.strftime("%d.%m.%Y %H:%M")
    updated = await ms.reschedule_meeting(meeting.id, new_start_at)
    if updated:
        new_time = new_start_at.strftime("%d.%m.%Y %H:%M")
        await message.answer(f"✅ Встреча «{meeting.title}» перенесена:\n{old_time} → {new_time}")
        _notify_meeting_changed(updated, f"⏰ Время: {old_time} → {new_time}")
    else:
        await message.answer("Не удалось перенести встречу.")


# ══════════════════════════════════════════════════════════════════
#  ОТМЕНА ВСТРЕЧИ
# ══════════════════════════════════════════════════════════════════


async def _handle_cancel_meeting(message, nlp_result: dict) -> None:
    from core.services.meeting_service import MeetingService
    ms = MeetingService()
    title = nlp_result.get("title", "")
    
    if not title:
        await message.answer("Укажите название встречи для отмены.")
        return

    meetings = await _find_meetings(message, title)
    if not meetings:
        await message.answer(f"Не найдена активная встреча «{title}».")
        return

    meeting = meetings[0]
    success = await ms.cancel_meeting(meeting.id)
    if success:
        await message.answer(f"✅ Встреча «{meeting.title}» отменена.")
        from celery_app.tasks.send_reminders import send_meeting_cancelled_notification
        send_meeting_cancelled_notification.delay(meeting.id)
    else:
        await message.answer("Не удалось отменить встречу.")


# ══════════════════════════════════════════════════════════════════
#  СОЗДАНИЕ ЗАДАЧИ
# ══════════════════════════════════════════════════════════════════


async def _handle_create_task(message, nlp_result: dict) -> None:
    from core.models import Message as DBMessage, Topic
    from core.services.task_service import TaskService
    from bot.handlers.tasks import notify_creator_about_unassigned_task

    title = nlp_result.get("title", "")
    if not title:
        await message.answer("Не удалось определить название задачи.")
        return

    username = nlp_result.get("username", "")
    due_date_str = nlp_result.get("new_date", nlp_result.get("date", ""))

    chat, topic, db_user = await _get_ctx(message)
    if not chat:
        await message.answer("Не удалось определить чат.")
        return

    target_topic = topic or (await sync_to_async(
        lambda: Topic.objects.get_or_create(chat=chat, thread_id=0, defaults={"is_active": True})
    )())[0]

    source_msg = await sync_to_async(DBMessage.objects.create)(
        telegram_msg_id=message.message_id, chat=chat, topic=target_topic,
        author=db_user, text=message.text or "", timestamp=message.date,
    )

    task_data = {
        "title": title,
        "assignees": [f"@{username}"] if username else [],
        "due_date": due_date_str if due_date_str else None,
        "description": "",
    }

    ts = TaskService()
    task = await ts._create_task_from_data(task_data, source_msg)
    if not task:
        await message.answer("Не удалось создать задачу.")
        return

    assignee_str = "не назначен"
    if task.assignees.exists():
        names = [f"@{a.user.username}" if a.user.username else (a.user.full_name or "?") for a in task.assignees.all()]
        assignee_str = ", ".join(names)
    due_str = task.due_date.strftime("%d.%m.%Y") if task.due_date else "без срока"

    await message.answer(
        f"✅ <b>{task.title}</b>\n👤 {assignee_str}\n📅 до {due_str}",
        parse_mode="HTML",
    )
    if not username:
        await notify_creator_about_unassigned_task(task)


# ══════════════════════════════════════════════════════════════════
#  СОЗДАНИЕ ВСТРЕЧИ
# ══════════════════════════════════════════════════════════════════


async def _handle_create_meeting(message, nlp_result: dict) -> None:
    from core.models import Message as DBMessage, Topic
    from core.services.meeting_service import MeetingService

    title = nlp_result.get("title", "")
    if not title:
        await message.answer("Не удалось определить название встречи.")
        return

    username = nlp_result.get("username", "")
    date_str = nlp_result.get("new_date", nlp_result.get("date", ""))
    time_str = nlp_result.get("new_time", "09:00")

    if not date_str:
        await message.answer("Не удалось определить дату встречи.")
        return

    chat, topic, db_user = await _get_ctx(message)
    if not chat:
        await message.answer("Не удалось определить чат.")
        return

    target_topic = topic or (await sync_to_async(
        lambda: Topic.objects.get_or_create(chat=chat, thread_id=0, defaults={"is_active": True})
    )())[0]

    source_msg = await sync_to_async(DBMessage.objects.create)(
        telegram_msg_id=message.message_id, chat=chat, topic=target_topic,
        author=db_user, text=message.text or "", timestamp=message.date,
    )

    meeting_data = {
        "title": title,
        "participants": [f"@{username}"] if username else [],
        "start_at": f"{date_str}T{time_str}",
        "description": "",
    }

    ms = MeetingService()
    meeting = await ms._create_meeting_from_data(meeting_data, source_msg)
    if not meeting:
        await message.answer("Не удалось создать встречу.")
        return

    p_str = "Все участники" if getattr(meeting, 'is_all_hands', False) else "не определены"
    if meeting.participants.exists():
        names = [f"@{p.username}" if p.username else (p.full_name or f"id={p.id}") for p in meeting.participants.all()]
        p_str = ", ".join(names)

    await message.answer(
        f"✅ <b>{meeting.title}</b>\n  ⏰ {meeting.start_at.strftime('%d.%m.%Y %H:%M')}\n  👥 {p_str}",
        parse_mode="HTML",
    )
    if username:
        from celery_app.tasks.send_reminders import send_meeting_assigned_notification
        send_meeting_assigned_notification.delay(meeting.id)


# ══════════════════════════════════════════════════════════════════
#  РЕДАКТИРОВАНИЕ ЗАДАЧИ
# ══════════════════════════════════════════════════════════════════


async def _handle_edit_task(message, nlp_result: dict) -> None:
    from core.services.task_service import TaskService
    from bot.handlers.tasks import _format_due_date, _format_assignees

    title = nlp_result.get("title", "")
    new_title_val = nlp_result.get("new_title", "")
    new_assignee = nlp_result.get("new_assignee", "")
    new_date_str = nlp_result.get("new_date", "")

    if not title:
        await message.answer("Укажите название задачи для редактирования.")
        return

    tasks = await _find_tasks(message, title)
    if not tasks:
        await message.answer(f"Не найдена активная задача.")
        return

    task = tasks[0]
    ts = TaskService()
    changes = []
    old_title = task.title

    if new_title_val:
        await ts.update_title(task.id, new_title_val)
        changes.append(f"✏️ Название: {old_title} → {new_title_val}")
        task.title = new_title_val

    if new_date_str:
        old_due = _format_due_date(task)
        await ts.update_due_date(task.id, new_date_str)
        new_due = _format_due_date(task)
        if old_due != new_due:
            changes.append(f"📅 Срок: {old_due} → {new_due}")

    if new_assignee:
        old_assign = _format_assignees(task)
        await ts.update_assignees(task.id, [f"@{new_assignee}"])
        new_assign = _format_assignees(task)
        if old_assign != new_assign:
            changes.append(f"👤 Исполнитель: {old_assign} → {new_assign}")

    if changes:
        task = await ts.get_task_by_id(task.id)
        await message.answer(f"✅ Задача обновлена\n" + "\n".join(changes), parse_mode="HTML")
    else:
        await message.answer("Нечего изменять.")


# ══════════════════════════════════════════════════════════════════
#  РЕДАКТИРОВАНИЕ ВСТРЕЧИ
# ══════════════════════════════════════════════════════════════════


async def _handle_edit_meeting(message, nlp_result: dict) -> None:
    from core.services.meeting_service import MeetingService
    from bot.handlers.meetings import _notify_meeting_changed, _format_meeting_time, _format_participants

    title = nlp_result.get("title", "")
    new_title_val = nlp_result.get("new_title", "")
    new_participant = nlp_result.get("new_assignee", "")
    new_date_str = nlp_result.get("new_date", "")
    new_time_str = nlp_result.get("new_time", "")

    if not title:
        await message.answer("Укажите название встречи для редактирования.")
        return

    meetings = await _find_meetings(message, title)
    if not meetings:
        await message.answer(f"Не найдена активная встреча.")
        return

    meeting = meetings[0]
    ms = MeetingService()
    changes = []
    old_title = meeting.title

    if new_title_val:
        await ms.update_title(meeting.id, new_title_val)
        changes.append(f"✏️ Название: {old_title} → {new_title_val}")
        meeting.title = new_title_val

    if new_date_str:
        old_time = _format_meeting_time(meeting)
        use_time = new_time_str if new_time_str else meeting.start_at.strftime("%H:%M")
        new_dt = await _parse_nlp_date(message, {"new_date": new_date_str, "new_time": use_time})
        if new_dt and new_dt > timezone.now():
            await ms.reschedule_meeting(meeting.id, new_dt)
            changes.append(f"⏰ Время: {old_time} → {new_dt.strftime('%d.%m.%Y %H:%M')}")
            meeting.start_at = new_dt

    if new_participant:
        old_part = _format_participants(meeting)
        await ms.update_participants(meeting.id, [f"@{new_participant}"])
        new_part = _format_participants(meeting)
        if old_part != new_part:
            changes.append(f"👥 Участники: {old_part} → {new_part}")

    if changes:
        meeting = await ms.get_meeting_by_id(meeting.id)
        await message.answer(f"✅ Встреча обновлена\n" + "\n".join(changes), parse_mode="HTML")
        _notify_meeting_changed(meeting, "\n".join(changes))
    else:
        await message.answer("Нечего изменять.")
