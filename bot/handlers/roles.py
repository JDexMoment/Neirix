"""
Команды управления ролями:
  /role              — показать свою роль
  /role list         — список участников чата
  /role set @user manager  — назначить роль (только admin)
  /role set @user member   — понизить (только admin)
"""
import logging
import re
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from asgiref.sync import sync_to_async

from bot.utils import get_chat_context
from core.models import UserRole, TelegramUser, TelegramChat
from core.services.permissions import user_can_manage_roles

logger = logging.getLogger(__name__)
router = Router()


def _format_role(role: str) -> str:
    labels = {
        "admin": "👑 Администратор",
        "manager": "⚙️ Менеджер",
        "member": "👤 Участник",
    }
    return labels.get(role, role)


def _can_be_assigned_by(target_role: str, actor_role: str) -> bool:
    """Проверяет, может ли actor_role назначать target_role."""
    RANK = {"admin": 3, "manager": 2, "member": 1}
    return RANK.get(actor_role, 0) > RANK.get(target_role, 0)


@router.message(Command("role"))
async def cmd_role(message: Message):
    """Обрабатывает /role, /role list, /role set @user role."""
    text = message.text or ""

    # /role list
    if text.strip().lower() == "/role list" or text.strip().lower() == "/role list@neirix_bot":
        await _role_list(message)
        return

    # /role set @username role | /role set@username role
    match = re.match(
        r"^/role\s+set\s+@(\w+)\s+(\w+)",
        text.strip(),
        re.IGNORECASE,
    )
    if match:
        await _role_set(message, match.group(1), match.group(2).lower())
        return

    # /role (показать свою роль)
    await _role_show(message)


async def _role_show(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not db_user or not chat:
        return

    role_qs = await sync_to_async(
        lambda: list(UserRole.objects.filter(user=db_user, chat=chat))
    )()
    if not role_qs:
        await message.answer(
            "Вы не привязаны к этому чату. "
            "Используйте код привязки из /link_chat."
        )
        return

    role_obj = role_qs[0]
    await message.answer(
        f"🎭 <b>Ваша роль в чате {chat.title}:</b>\n"
        f"{_format_role(role_obj.role)}",
        parse_mode="HTML",
    )


async def _role_list(message: Message):
    chat, topic, db_user = await get_chat_context(message)
    if not db_user or not chat:
        return

    roles = await sync_to_async(
        lambda: list(
            UserRole.objects.filter(chat=chat)
            .select_related("user")
            .order_by("-role", "user__full_name")
        )
    )()

    if not roles:
        await message.answer("В этом чате пока нет участников с ролями.")
        return

    lines = [f"🎭 <b>Участники чата {chat.title}:</b>"]
    for r in roles:
        user = r.user
        name = f"@{user.username}" if user.username else user.full_name
        lines.append(f"• {_format_role(r.role)} — {name}")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def _role_set(message: Message, target_username: str, new_role: str):
    """Назначает роль пользователю @target_username."""
    if new_role not in ("admin", "manager", "member"):
        await message.answer(
            "❌ Некорректная роль. Допустимые: admin, manager, member.\n"
            "Пример: <code>/role set @username manager</code>",
            parse_mode="HTML",
        )
        return

    chat, topic, actor_user = await get_chat_context(message)
    if not actor_user or not chat:
        return

    # Только admin может управлять ролями
    can_manage = await sync_to_async(user_can_manage_roles)(actor_user, chat)
    if not can_manage:
        await message.answer(
            "❌ У вас недостаточно прав. Только администраторы "
            "чата могут назначать роли."
        )
        return

    # Ищем целевого пользователя
    target_user = await sync_to_async(
        lambda: TelegramUser.objects.filter(
            username__iexact=target_username
        ).first()
    )()
    if not target_user:
        await message.answer(
            f"❌ Пользователь @{target_username} не найден в базе. "
            f"Он должен хотя бы раз написать боту."
        )
        return

    # Проверяем, существует ли уже роль
    target_role_obj = await sync_to_async(
        lambda: UserRole.objects.filter(
            user=target_user, chat=chat
        ).first()
    )()

    if target_role_obj and not _can_be_assigned_by(new_role, target_role_obj.role):
        # Нельзя понизить того, кто выше/равен по рангу
        if target_role_obj.role == "admin" and new_role != "admin":
            await message.answer("❌ Вы не можете изменить роль другого администратора.")
            return

    # Обновляем или создаём роль
    def _upsert_role():
        role, created = UserRole.objects.update_or_create(
            user=target_user,
            chat=chat,
            defaults={"role": new_role},
        )
        return role, created

    role_obj, was_created = await sync_to_async(_upsert_role)()

    if was_created:
        await message.answer(
            f"✅ Пользователю @{target_username} назначена роль "
            f"{_format_role(new_role)} в чате «{chat.title}»."
        )
    else:
        await message.answer(
            f"✅ Роль пользователя @{target_username} изменена на "
            f"{_format_role(new_role)} в чате «{chat.title}»."
        )
