import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

logger = logging.getLogger(__name__)

FSM_TIMEOUT_SECONDS = 120  # 2 минуты


class FSMTimeoutMiddleware(BaseMiddleware):
    """
    Если пользователь находится в FSM-состоянии дольше таймаута,
    состояние сбрасывается автоматически.
    """

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        state: FSMContext = data.get("state")
        if not state:
            return await handler(event, data)

        current_state = await state.get_state()
        if current_state is None:
            return await handler(event, data)

        fsm_data = await state.get_data()
        started_at = fsm_data.get("_fsm_started_at")

        if started_at and (time.time() - started_at) > FSM_TIMEOUT_SECONDS:
            logger.info(
                "FSM timeout for user=%s state=%s",
                event.from_user.id if event.from_user else "?",
                current_state,
            )
            await state.clear()
            await event.answer("⏱ Время ожидания истекло. Операция отменена.")
            return None

        return await handler(event, data)