"""
Хендлеры для подтверждения участия во встречах.
Кнопки: ✅ Буду / ❌ Не смогу / 📋 Кто идёт?
"""
import logging

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.conf import settings

from core.models import MeetingAttendance, Meeting, TelegramUser
from bot.keyboards.inline import (
    meeting_attendance_status_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()


@sync_to_async
def _get_meeting_and_user(meeting_id: int, telegram_id: int):
    """Возвращает (meeting, user) или (None, None)."""
    try:
        meeting = Meeting.objects.filter(id=meeting_id).select_related("creator").first()
        user = TelegramUser.objects.filter(telegram_id=telegram_id).first()
        return meeting, user
    except Exception:
        return None, None


@sync_to_async
def _set_attendance(meeting_id: int, user_id: int, status: str) -> bool:
    try:
        user = TelegramUser.objects.filter(telegram_id=user_id).first()
        if not user:
            return False
        meeting = Meeting.objects.filter(id=meeting_id).first()
        if not meeting:
            return False
        attendance, created = MeetingAttendance.objects.get_or_create(
            meeting=meeting,
            user=user,
            defaults={'status': status, 'responded_at': timezone.now()},
        )
        if not created:
            attendance.status = status
            attendance.responded_at = timezone.now()
            attendance.save(update_fields=['status', 'responded_at'])
        return True
    except Exception as e:
        logger.error("Attendance error: %s", e, exc_info=True)
        return False


@sync_to_async
def _get_attendance_status(meeting_id: int) -> dict:
    result = {'confirmed': [], 'declined': [], 'pending': []}
    try:
        meeting = Meeting.objects.filter(id=meeting_id).prefetch_related('participants', 'attendances').first()
        if not meeting:
            return result
        participants = list(meeting.participants.all())
        attendances = {a.user_id: a for a in meeting.attendances.all()}

        for user in participants:
            att = attendances.get(user.id)
            if att and att.status == MeetingAttendance.CONFIRMED:
                result['confirmed'].append(user)
            elif att and att.status == MeetingAttendance.DECLINED:
                result['declined'].append(user)
            else:
                result['pending'].append(user)
        return result
    except Exception as e:
        logger.error("Get attendance error: %s", e, exc_info=True)
        return result


def _format_user_list(users: list) -> str:
    names = []
    for u in users:
        if u.username:
            names.append(f"@{u.username}")
        elif u.full_name:
            names.append(u.full_name)
        else:
            names.append(f"id={u.id}")
    return ", ".join(names) if names else "—"


async def _notify_creator(meeting, action_user, status: str):
    """Уведомляет создателя встречи о подтверждении/отказе."""
    if not meeting.creator:
        return
    if meeting.creator.id == action_user.id:
        return  # Не уведомлять самого себя

    user_str = f"@{action_user.username}" if action_user.username else action_user.full_name
    meeting_time = timezone.localtime(meeting.start_at).strftime("%d.%m %H:%M") if meeting.start_at else ""
    emoji = "✅" if status == MeetingAttendance.CONFIRMED else "❌"
    text = f"{emoji} <b>{user_str}</b> — {meeting.title} ({meeting_time})"

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(
            meeting.creator.telegram_id,
            text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Failed to notify creator %s: %s", meeting.creator.id, e)
    finally:
        await bot.session.close()


async def _show_attendance_status(callback: CallbackQuery, meeting_id: int):
    stats = await _get_attendance_status(meeting_id)
    meeting = await _get_meeting_and_user(meeting_id, 0)
    meeting = meeting[0]
    if not meeting:
        await callback.answer("Встреча не найдена.", show_alert=True)
        return

    confirmed_str = _format_user_list(stats['confirmed'])
    declined_str = _format_user_list(stats['declined'])
    pending_str = _format_user_list(stats['pending'])
    confirmed_count = len(stats['confirmed'])
    declined_count = len(stats['declined'])
    pending_count = len(stats['pending'])

    text = (
        f"📊 <b>{meeting.title}</b>\n"
        f"⏰ {meeting.start_at.strftime('%d.%m.%Y %H:%M') if meeting.start_at else ''}\n\n"
        f"✅ <b>Будут</b> ({confirmed_count}): {confirmed_str}\n"
        f"❌ <b>Отказались</b> ({declined_count}): {declined_str}\n"
        f"⏳ <b>Не ответили</b> ({pending_count}): {pending_str}"
    )

    # ═══ Отправляем НОВЫМ сообщением, а не редактируем оригинал ═══
    await callback.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=meeting_attendance_status_keyboard(meeting_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("meeting_confirm:"))
async def callback_meeting_confirm(callback: CallbackQuery):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    success = await _set_attendance(meeting_id, callback.from_user.id, MeetingAttendance.CONFIRMED)
    if success:
        await callback.answer("✅ Вы будете на встрече!")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        # ═══ Уведомляем создателя ═══
        meeting, user = await _get_meeting_and_user(meeting_id, callback.from_user.id)
        if meeting and user:
            await _notify_creator(meeting, user, MeetingAttendance.CONFIRMED)
    else:
        await callback.answer("❌ Не удалось подтвердить.", show_alert=True)


@router.callback_query(F.data.startswith("meeting_decline:"))
async def callback_meeting_decline(callback: CallbackQuery):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return

    success = await _set_attendance(meeting_id, callback.from_user.id, MeetingAttendance.DECLINED)
    if success:
        await callback.answer("❌ Отмечено. Вы не сможете присутствовать.")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        # ═══ Уведомляем создателя ═══
        meeting, user = await _get_meeting_and_user(meeting_id, callback.from_user.id)
        if meeting and user:
            await _notify_creator(meeting, user, MeetingAttendance.DECLINED)
    else:
        await callback.answer("❌ Не удалось отметить.", show_alert=True)


@router.callback_query(F.data.startswith("meeting_attendance_status:"))
async def callback_meeting_attendance_status(callback: CallbackQuery):
    try:
        meeting_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return
    await _show_attendance_status(callback, meeting_id)
