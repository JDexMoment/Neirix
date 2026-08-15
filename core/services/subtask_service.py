"""
Сервис подзадач (SubTask).

- Создание подзадач для задачи (при создании задачи из NLP и вручную).
- Смена статуса подзадачи (pending -> in_progress -> done -> pending).
- Назначение исполнителей на подзадачу.
- Рендеринг блока подзадач с прогресс-баром (только из уже загруженного кэша prefetch).

ВАЖНО: все ORM-вызовы обёрнуты в sync_to_async, т.к. сервис вызывается из async-контекста
(Celery worker / aiogram handlers). Никаких прямых Task.objects... вне sync_to_async.
"""
import logging
import re
from calendar import monthrange
from datetime import datetime, timedelta
from typing import List, Optional

from django.db.models import Q
from django.utils import timezone
from asgiref.sync import sync_to_async

from core.models import SubTask, Task, TelegramUser

logger = logging.getLogger(__name__)

STATUS_ICONS = {
    "done": "✅",
    "in_progress": "🔄",
    "pending": "⬜️",
}
STATUS_LABELS = {
    "done": "✅ Готово",
    "in_progress": "🔄 В работе",
    "pending": "⬜️ Ожидает",
}
PROGRESS_BLOCKS = 10


# ─────────────────────────────────────────────────────────────────────
# Синхронные хелперы (всегда вызываются через sync_to_async)
# ─────────────────────────────────────────────────────────────────────

def _find_user_by_username(clean_name: str) -> Optional[TelegramUser]:
    return TelegramUser.objects.filter(
        Q(username__iexact=clean_name) | Q(full_name__icontains=clean_name)
    ).first()


def _parse_sub_due(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        naive = datetime.strptime(str(raw)[:10], "%Y-%m-%d")
        naive = naive.replace(hour=23, minute=59, second=0, microsecond=0)
        tz = timezone.get_current_timezone()
        return timezone.make_aware(naive, tz)
    except (ValueError, TypeError):
        return None


def _correct_past_date(due):
    """Если дата сильно в прошлом (>7 дней) — сдвигаем на следующий месяц (как для задач)."""
    if due and due < timezone.now() - timedelta(days=7):
        new_month = due.month + 1
        new_year = due.year
        if new_month > 12:
            new_month = 1
            new_year += 1
        last_day = monthrange(new_year, new_month)[1]
        new_day = min(due.day, last_day)
        return due.replace(year=new_year, month=new_month, day=new_day,
                            hour=23, minute=59, second=0, microsecond=0)
    return due


def _split_project_title(title: str):
    """
    Парсит 'Проект: a, b, c' или 'Проект — a; b' в (родитель, [подзадачи]).
    Возвращает (None, []) если это не проект-список.
    Не срабатывает на обычных двоеточиях без списка ('Встреча: обсуждение').
    """
    # Берём ПОСЛЕДНЮЮ точку-разделитель, т.к. префикс вида
    # '[14:56] Автор:' тоже содержит двоеточие. [^:—–]* гарантирует,
    # что совпадение — самое правое двоеточие.
    m = re.search(r'[:—–]\s*([^:—–]*)$', title)
    if not m:
        return None, []
    parent = title[:m.start()].strip()
    rest = m.group(1).strip()
    # Обязателен разделитель-список, иначе это не проект
    if not re.search(r'[;,]\s*|\n|•|\u2022|\t', rest):
        return None, []
    items = re.split(r'[;,]\s*|\n|•|\u2022|\t', rest)
    items = [i.strip().strip('"\'-•').strip() for i in items if i.strip()]
    if len(items) < 2:
        return None, []
    return parent, items


# ─────────────────────────────────────────────────────────────────────
# Async API
# ─────────────────────────────────────────────────────────────────────

def _parse_sub_item(raw: str):
    """Из строки подзадачи извлекает (название, [@usernames], due_date).

    Поддерживает: 'написать код до 30 числа @JDexMoment', 'погладить Дасю @D1MRUS до 18',
    'выгрузить сайт до 25 @D1MRUS', 'продать за 1м до 30 @JDexMoment'.
    """
    raw = (raw or "").strip()
    users = re.findall(r"@(\w+)", raw)

    due = None
    now = timezone.localtime(timezone.now())
    # "до ДД.ММ"
    m = re.search(r"до\s+(\d{1,2})\.(\d{1,2})", raw)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = now.year
        try:
            due = timezone.make_aware(
                datetime(year, month, day, 23, 59, 0), timezone.get_current_timezone()
            )
        except ValueError:
            due = None
    # "до 30 числа" / "до 18"
    if due is None:
        m = re.search(r"до\s+(\d{1,2})(?:\s*числа)?", raw)
        if m:
            day = int(m.group(1))
            month, year = now.month, now.year
            if day < now.day:
                month += 1
                if month > 12:
                    month, year = 1, year + 1
            try:
                due = timezone.make_aware(
                    datetime(year, month, day, 23, 59, 0), timezone.get_current_timezone()
                )
            except ValueError:
                due = None

    title = raw
    for u in users:
        title = title.replace(f"@{u}", "")
    title = re.sub(r"до\s+\d{1,2}(?:\s*числа)?", "", title)
    title = re.sub(r"до\s+\d{1,2}\.\d{1,2}", "", title)
    title = re.sub(r"\s+", " ", title).strip(" \t-–—,:")
    return title, users, due


async def create_subtasks_for_task(task: Task, subtasks_data) -> List[SubTask]:
    """Создаёт подзадачи для задачи. subtasks_data: список dict {title, assignees, due_date}
    или список строк (в этом случае из строки извлекаются @username и срок)."""
    created: List[SubTask] = []
    order = 0
    for sd in (subtasks_data or []):
        if isinstance(sd, dict):
            title = (sd.get("title") or "").strip()
            due = _correct_past_date(_parse_sub_due(sd.get("due_date")))
            raw_users = sd.get("assignees") or []
        else:
            title, raw_users, due = _parse_sub_item(str(sd))
            due = _correct_past_date(due)
        if not title:
            continue

        st = await sync_to_async(SubTask.objects.create)(
            parent_task=task,
            title=title,
            description=(sd.get("description", "") if isinstance(sd, dict) else ""),
            due_date=due,
            status="pending",
            order=order,
        )
        order += 1

        users = []
        for raw in raw_users:
            clean = str(raw).lstrip("@").strip()
            if not clean:
                continue
            u = await sync_to_async(_find_user_by_username)(clean)
            if u:
                users.append(u)
        if users:
            await sync_to_async(st.assignees.set)(users)

        created.append(st)

    if created:
        logger.info("Subtasks created | task_id=%s count=%s", task.id, len(created))
    return created


async def add_subtask(
    task_id: int,
    title: str,
    due_date_str: Optional[str] = None,
    assignee_usernames: Optional[List[str]] = None,
) -> Optional[SubTask]:
    """Добавляет одну подзадачу (кнопка '➕ Подзадача')."""
    title = (title or "").strip()
    if not title:
        return None
    due = _correct_past_date(_parse_sub_due(due_date_str))

    def _create():
        task = Task.objects.get(id=task_id)
        order = task.subtasks.count()
        return SubTask.objects.create(
            parent_task=task,
            title=title,
            due_date=due,
            status="pending",
            order=order,
        )

    try:
        st = await sync_to_async(_create)()
    except Task.DoesNotExist:
        return None

    users = []
    for raw in (assignee_usernames or []):
        clean = str(raw).lstrip("@").strip()
        if not clean:
            continue
        u = await sync_to_async(_find_user_by_username)(clean)
        if u:
            users.append(u)
    if users:
        await sync_to_async(st.assignees.set)(users)
    return st


async def set_subtask_status(task_id: int, sub_id: int, status: str) -> bool:
    """Устанавливает статус подзадачи. Цикл: pending -> in_progress -> done -> pending.

    Возвращает True, если после изменения задача стала полностью выполненной
    (все подзадачи done -> задача помечается done).
    """
    valid = {"pending", "in_progress", "done"}
    if status not in valid:
        return False

    def _update():
        try:
            st = SubTask.objects.get(id=sub_id, parent_task_id=task_id)
            st.status = status
            st.completed_at = timezone.now() if status == "done" else None
            st.save(update_fields=["status", "completed_at"])
            task = st.parent_task
            subs = list(task.subtasks.all())
            if subs and all(s.status == "done" for s in subs) and task.status != "done":
                task.status = "done"
                task.completed_at = timezone.now()
                task.save(update_fields=["status", "completed_at"])
                return True
            return False
        except SubTask.DoesNotExist:
            return False

    return await sync_to_async(_update)()


async def assign_subtask_users(task_id: int, sub_id: int, usernames: List[str]) -> bool:
    """Переназначает исполнителей подзадачи (заменяет текущий набор)."""
    def _update():
        try:
            st = SubTask.objects.get(id=sub_id, parent_task_id=task_id)
            st.assignees.clear()
            for raw in usernames:
                clean = str(raw).lstrip("@").strip()
                if not clean:
                    continue
                u = _find_user_by_username(clean)
                if u:
                    st.assignees.add(u)
            return True
        except SubTask.DoesNotExist:
            return False

    return await sync_to_async(_update)()


# ─────────────────────────────────────────────────────────────────────
# Рендеринг (читает ТОЛЬКО уже загруженный кэш prefetch — без доп. запросов)
# ─────────────────────────────────────────────────────────────────────

def _format_sub_assignees(sub) -> str:
    users = list(sub.assignees.all())
    if not users:
        return ""
    return " " + " ".join(
        f"@{u.username}" if u.username else (u.full_name or f"id={u.id}") for u in users
    )


def build_subtasks_section(task, priority_emoji: str = "") -> str:
    """
    Возвращает блок подзадач с прогресс-баром, например:

    📋 Проект "Релиз v2.0" [████░░░░] 50%

    @JDex, @D1MRUS, @Dasha, @Anya
      ✅ Написать код @JDex до 13.08.2026
      🔄 Документация @Dasha до 15.08.2026
      ⬜️ Деплой @Anya до 20.08.2026
    """
    subs = list(task.subtasks.all())
    if not subs:
        return ""

    total = len(subs)
    done = sum(1 for s in subs if s.status == "done")
    pct = round(done / total * 100)
    filled = round(pct / 100 * PROGRESS_BLOCKS)
    bar = "█" * filled + "░" * (PROGRESS_BLOCKS - filled)

    lines = [f"{priority_emoji}📋 <b>{task.title}</b> [{bar}] {pct}%", ""]

    # Общий список исполнителей (объединение по всем подзадачам)
    seen = set()
    union = []
    for s in subs:
        for u in s.assignees.all():
            if u.id not in seen:
                seen.add(u.id)
                union.append(u)
    if union:
        lines.append("  " + ", ".join(
            f"@{u.username}" if u.username else (u.full_name or f"id={u.id}") for u in union
        ))

    for s in subs:
        icon = STATUS_ICONS.get(s.status, "⬜️")
        line = f"  {icon} {s.title}"
        line += _format_sub_assignees(s)
        if s.due_date:
            dt = s.due_date
            if timezone.is_aware(dt):
                dt = timezone.localtime(dt)
            line += f" до {dt.strftime('%d.%m.%Y')}"
        lines.append(line)

    return "\n".join(lines)


def progress_percent(task) -> int:
    subs = list(task.subtasks.all())
    if not subs:
        return 0
    done = sum(1 for s in subs if s.status == "done")
    return round(done / len(subs) * 100)
