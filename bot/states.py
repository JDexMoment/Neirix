from aiogram.fsm.state import State, StatesGroup


class RescheduleMeetingStates(StatesGroup):
    """Состояния для переноса встречи."""
    waiting_for_new_datetime = State()


class EditTaskStates(StatesGroup):
    """Состояния для редактирования задачи."""
    waiting_for_choice = State()
    waiting_for_due_date = State()
    waiting_for_assignee = State()
    waiting_for_title = State()


class AssignTaskStates(StatesGroup):
    """Состояния для назначения исполнителя задачи без assignee."""
    waiting_for_assignee_username = State()


class EditMeetingStates(StatesGroup):
    """Состояния для редактирования встречи."""
    waiting_for_choice = State()
    waiting_for_participants = State()
    waiting_for_title = State()
