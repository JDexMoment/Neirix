"""
Сервис недоступности пользователей.
Проверка/создание/отмена периодов недоступности,
фильтрация списков исполнителей и участников встреч.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from django.utils import timezone
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


def is_absent_now_sync(user, at: Optional[datetime] = None) -> Optional[object]:
    """Синхронная проверка. Возвращает UserAbsence или None."""
    from core.models import UserAbsence
    at = at or timezone.now()
    return UserAbsence.objects.filter(
        user=user, start__lte=at, end__gte=at,
    ).order_by("-end").first()


def get_absent_users_sync(users: List, at: Optional[datetime] = None) -> List:
    """Разделяет список на доступных. Возвращает список НЕдоступных."""
    from core.models import UserAbsence
    if not users:
        return []
    at = at or timezone.now()
    absent_ids = set(
        UserAbsence.objects.filter(
            user__in=users, start__lte=at, end__gte=at,
        ).values_list("user_id", flat=True)
    )
    return [u for u in users if u.id in absent_ids]


def format_absence(absence) -> str:
    """Человекочитаемый текст недоступности."""
    end = timezone.localtime(absence.end) if timezone.is_aware(absence.end) else absence.end
    reason = f" ({absence.reason})" if absence.reason else ""
    return f"недоступен до {end.strftime('%d.%m.%Y')}{reason}"


class AbsenceService:
    # ── Проверки ──────────────────────────────────────────────
    async def is_absent(self, user, at=None):
        """Возвращает UserAbsence или None."""
        return await sync_to_async(is_absent_now_sync)(user, at)

    async def filter_absent_assignees(self, users: List, at=None) -> List:
        return await sync_to_async(get_absent_users_sync)(users, at)

    async def get_absent_participants(self, meeting, at=None) -> List:
        """Недоступные участники встречи (для создания инстансов серии)."""
        participants = await sync_to_async(list)(meeting.participants.all())
        return await self.filter_absent_assignees(participants, at)

    # ── Управление ────────────────────────────────────────────
    async def create_absence(self, user, start: datetime, end: datetime,
                             reason: str = "") -> object:
        """Создаёт период недоступности. Разрешает пересечения."""
        from core.models import UserAbsence
        if timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.get_current_timezone())
        if timezone.is_naive(end):
            end = timezone.make_aware(end, timezone.get_current_timezone())
        if end <= start:
            raise ValueError("Конец недоступности должен быть позже начала")
        absence = await sync_to_async(UserAbsence.objects.create)(
            user=user, start=start, end=end, reason=reason,
        )
        logger.info("Absence created | user=%s until=%s", user, end)
        return absence

    async def cancel_absence(self, absence_id: int, user=None) -> bool:
        from core.models import UserAbsence
        def _cancel():
            qs = UserAbsence.objects.filter(id=absence_id)
            if user:
                qs = qs.filter(user=user)
            return qs.delete()[0] > 0
        return await sync_to_async(_cancel)()

    async def cancel_all_active(self, user) -> int:
        """Отменяет все активные недоступности пользователя (/back)."""
        from core.models import UserAbsence
        now = timezone.now()
        def _cancel():
            return UserAbsence.objects.filter(
                user=user, end__gte=now,
            ).delete()[0]
        return await sync_to_async(_cancel)()

    async def get_active_absences(self, user) -> List:
        from core.models import UserAbsence
        now = timezone.now()
        return await sync_to_async(lambda: list(
            UserAbsence.objects.filter(user=user, end__gte=now).order_by("end")
        ))()


# ── Парсер дат для /away ─────────────────────────────────────
def parse_absence_duration(text: str) -> Optional[datetime]:
    """
    Парсит аргумент /away и возвращает дату окончания (23:59).
    Поддерживает:
      до 25.05 / до 25.05.2027 / до 2027-05-25
      на 3 дня / на 2 недели / на неделю / на месяц
    """
    import re
    text = (text or "").strip().lower()
    if not text:
        return None

    tz = timezone.get_current_timezone()
    now = timezone.localtime(timezone.now())
    end_date = None

    # ── "до ДД.ММ" или "до ДД.ММ.ГГГГ" ──
    m = re.search(r"до\s+(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        if year < 100:
            year += 2000
        try:
            end_date = datetime(year, month, day)
        except ValueError:
            return None
        # Если дата уже прошла — считаем следующий год
        if end_date.date() < now.date() and not m.group(3):
            end_date = end_date.replace(year=year + 1)
        return timezone.make_aware(
            end_date.replace(hour=23, minute=59, second=0, microsecond=0), tz
        )

    # ── "до ГГГГ-ММ-ДД" ──
    m = re.search(r"до\s+(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            end_date = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return timezone.make_aware(
            end_date.replace(hour=23, minute=59, second=0, microsecond=0), tz
        )

    # ── "до завтра / послезавтра" ──
    if "до послезавтра" in text:
        end_date = now + timedelta(days=2)
    elif "до завтра" in text:
        end_date = now + timedelta(days=1)
    elif re.search(r"\bна\s+(\d+)\s+дн", text):
        days = int(re.search(r"\bна\s+(\d+)\s+дн", text).group(1))
        end_date = now + timedelta(days=days)
    elif re.search(r"\bна\s+(\d+)\s+недел", text):
        weeks = int(re.search(r"\bна\s+(\d+)\s+недел", text).group(1))
        end_date = now + timedelta(weeks=weeks)
    elif re.search(r"\bна\s+недел", text):
        end_date = now + timedelta(weeks=1)
    elif re.search(r"\bна\s+месяц", text):
        end_date = now + timedelta(days=30)
    elif re.search(r"\bна\s+(\d+)\s+час", text):
        hours = int(re.search(r"\bна\s+(\d+)\s+час", text).group(1))
        return timezone.make_aware(
            (now + timedelta(hours=hours)).replace(second=0, microsecond=0), tz
        )

    if end_date is None:
        return None

    naive = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 0)
    return timezone.make_aware(naive, tz)