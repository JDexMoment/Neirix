"""
Управление недоступностью пользователей.
/away до 25.05      — недоступен до даты
/away на неделю     — недоступен на период
/away список        — активные недоступности
/back               — снять все недоступности
+ callback force_assign_task/meeting/skip (кнопки «всё равно»)
+ callback absence_cancel
"""
import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from asgiref.sync import sync_to_async
from django.utils import timezone

from bot.utils import get_chat_context
from bot.keyboards.inline import force_assign_keyboard, absence_cancel_keyboard
from core.models import TelegramUser, TaskAssignee, Task, Meeting, PendingAssignment
from core.services.absence_service import (
    AbsenceService, parse_absence_duration, format_absence,
)

logger = logging.getLogger(__name__)
router = Router()
absence_service = AbsenceService()


@router.message(Command("away"))
async def cmd_away(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    text = (message.text or "").strip()
    args = text[len("/away"):].strip()

    # /away список
    if args.lower() in ("список", "list", "активные"):
        absences = await absence_service.get_active_absences(db_user)
        if not absences:
            await message.answer("✅ У вас нет активных периодов недоступности.")
            return
        lines = ["🚫 <b>Ваши периоды недоступности:</b>", ""]
        for a in absences:
            end = timezone.localtime(a.end) if timezone.is_aware(a.end) else a.end
            reason = f" — {a.reason}" if a.reason else ""
            lines.append(f"• до {end.strftime('%d.%m.%Y %H:%M')}{reason}")
        lines.append("")
        lines.append("<i>/back — снять все</i>")
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    end_date = parse_absence_duration(args)
    if not end_date:
        await message.answer(
            "🚫 <b>Недоступность</b>\n\n"
            "Пока бот не будет назначать вам задачи и слать напоминания.\n\n"
            "Примеры:\n"
            "• <code>/away до 25.05</code>\n"
            "• <code>/away до 25.05.2027</code>\n"
            "• <code>/away на 3 дня</code>\n"
            "• <code>/away на 2 недели</code>\n"
            "• <code>/away на месяц</code>\n\n"
            "<code>/away список</code> — активные периоды",
            parse_mode="HTML",
        )
        return

    start = timezone.now()
    absence = await absence_service.create_absence(db_user, start, end_date)

    end_local = timezone.localtime(absence.end)
    await message.answer(
        f"🚫 Вы <b>недоступны</b> до <b>{end_local.strftime('%d.%m.%Y %H:%M')}</b>.\n\n"
        f"Бот не будет назначать вам задачи и слать напоминания.\n"
        f"<i>/back — вернуться досрочно</i>",
        parse_mode="HTML",
        reply_markup=absence_cancel_keyboard(absence.id),
    )


@router.message(Command("back"))
async def cmd_back(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not db_user:
        await message.answer("Не удалось определить пользователя.")
        return

    deleted = await absence_service.cancel_all_active(db_user)
    if deleted:
        await message.answer(f"✅ Снова доступны! Снято периодов: {deleted}.")
    else:
        await message.answer("✅ Вы и так доступны.")


# ═══ Кнопка «Я снова доступен» из /away ═══
@router.callback_query(F.data.startswith("absence_cancel:"))
async def callback_absence_cancel(callback: CallbackQuery):
    try:
        absence_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка.", show_alert=True)
        return

    db_user = await sync_to_async(
        lambda: TelegramUser.objects.filter(telegram_id=callback.from_user.id).first()
    )()
    if not db_user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    ok = await absence_service.cancel_absence(absence_id, user=db_user)
    if ok:
        await callback.answer("✅ Вы снова доступны!")
        try:
            await callback.message.edit_text("✅ Недоступность снята.")
        except Exception:
            pass
    else:
        await callback.answer("Не найдено или уже снято.", show_alert=True)


# ═══ «Всё равно назначить» — задача ═══
@router.callback_query(F.data.startswith("force_assign_task:"))
async def callback_force_assign_task(callback: CallbackQuery):
    try:
        pending_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка.", show_alert=True)
        return

    pending = await sync_to_async(
        lambda: PendingAssignment.objects.filter(id=pending_id, kind="task")
        .select_related("task", "user").first()
    )()
    if not pending or not pending.task:
        await callback.answer("Назначение уже обработано.", show_alert=True)
        return

    task, user = pending.task, pending.user
    # Идемпотентность: не создаём дубль
    exists = await sync_to_async(
        lambda: TaskAssignee.objects.filter(task=task, user=user).exists()
    )()
    if not exists:
        await sync_to_async(TaskAssignee.objects.create)(task=task, user=user)

    await sync_to_async(pending.delete)()
    await callback.answer(f"✅ Задача назначена на @{user.username or user.full_name}")

    # Уведомляем самого исполнителя
    try:
        from celery_app.tasks.send_reminders import send_task_assigned_notification
        send_task_assigned_notification.delay(task.id)
    except Exception as e:
        logger.warning("Failed to notify after force-assign: %s", e)

    try:
        await callback.message.edit_text(
            f"💪 Задача «{task.title}» назначена на "
            f"@{user.username or user.full_name}",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ═══ «Всё равно участвует» — встреча ═══
@router.callback_query(F.data.startswith("force_assign_meeting:"))
async def callback_force_assign_meeting(callback: CallbackQuery):
    try:
        pending_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка.", show_alert=True)
        return

    pending = await sync_to_async(
        lambda: PendingAssignment.objects.filter(id=pending_id, kind="meeting")
        .select_related("meeting", "user").first()
    )()
    if not pending or not pending.meeting:
        await callback.answer("Назначение уже обработано.", show_alert=True)
        return

    meeting, user = pending.meeting, pending.user
    await sync_to_async(meeting.participants.add)(user)
    await sync_to_async(pending.delete)()
    await callback.answer(f"✅ @{user.username or user.full_name} участвует во встрече")

    try:
        from celery_app.tasks.send_reminders import send_meeting_assigned_notification
        send_meeting_assigned_notification.delay(meeting.id)
    except Exception as e:
        logger.warning("Failed to notify after force-assign: %s", e)

    try:
        await callback.message.edit_text(
            f"💪 Встреча «{meeting.title}»: @{user.username or user.full_name} участвует",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ═══ «Пропустить» ═══
@router.callback_query(F.data.startswith("force_assign_skip:"))
async def callback_force_assign_skip(callback: CallbackQuery):
    try:
        pending_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка.", show_alert=True)
        return

    await sync_to_async(
        lambda: PendingAssignment.objects.filter(id=pending_id).delete()
    )()
    await callback.answer("Пропущено.")
    try:
        await callback.message.edit_text("↩️ Назначение пропущено.")
    except Exception:
        pass