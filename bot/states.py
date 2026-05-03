from aiogram.fsm.state import State, StatesGroup


class RescheduleMeetingStates(StatesGroup):
    waiting_for_new_datetime = State()