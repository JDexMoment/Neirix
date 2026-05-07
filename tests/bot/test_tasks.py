import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_message


def _make_mock_task(key, title, task_id=1, due_date=None, assignees=None):
    task = MagicMock()
    task.id = task_id
    task.title = title
    task.due_date = due_date
    task.status = "open"

    links = []
    for user in (assignees or []):
        link = MagicMock()
        link.user = user
        links.append(link)
    task.assignees.all.return_value = links
    return task


def _make_mock_user(username=None, full_name="Unknown", user_id=1):
    u = MagicMock()
    u.id = user_id
    u.username = username
    u.full_name = full_name
    return u


@pytest.fixture
def tasks_with_assignees():
    u1 = _make_mock_user(username="user1", full_name="User One")
    u2 = _make_mock_user(username=None, full_name="User Two")
    return [
        _make_mock_task(
            "report", "Сделать отчёт",
            task_id=1,
            due_date=datetime.now(dt_timezone.utc) + timedelta(days=2),
            assignees=[u1],
        ),
        _make_mock_task(
            "pres", "Подготовить презентацию",
            task_id=2,
            assignees=[u1, u2],
        ),
    ]


@pytest.fixture
def tasks_no_assignees():
    return [_make_mock_task("common", "Общая задача", task_id=3)]


@pytest.fixture
def mock_get_chat_context_tasks():
    with patch("bot.handlers.tasks.get_chat_context") as mock:
        yield mock


@pytest.fixture
def mock_sync_tasks():
    with patch("bot.handlers.tasks.sync_to_async") as mock_s2a:
        yield mock_s2a


def _setup_sync_mock(mock_s2a, return_value):
    async def fake_fetch(*a, **kw):
        return return_value
    mock_s2a.return_value = fake_fetch


# ──────────────────────────────────────────────────────────────────────
# /tasks — список задач
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tasks_private_with_tasks(
    private_chat, telegram_user, now_dt,
    tasks_with_assignees,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    msg = make_message(private_chat, telegram_user, "/tasks", now_dt)
    mock_chat = MagicMock()
    mock_db_user = MagicMock()
    mock_get_chat_context_tasks.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_tasks, tasks_with_assignees)

    await cmd_tasks(msg)

    # 1 заголовок + 2 задачи = 3 вызова
    assert msg.answer.call_count == 3
    texts = [
        call.args[0] if call.args else call.kwargs.get("text", "")
        for call in msg.answer.call_args_list
    ]

    assert "📋 Ваши задачи" in texts[0]
    assert "Сделать отчёт" in texts[1]
    assert "@user1" in texts[1]
    assert "Подготовить презентацию" in texts[2]
    assert "User Two" in texts[2]


@pytest.mark.asyncio
async def test_tasks_private_no_tasks(
    private_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    msg = make_message(private_chat, telegram_user, "/tasks", now_dt)
    mock_chat = MagicMock()
    mock_db_user = MagicMock()
    mock_get_chat_context_tasks.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_tasks, [])

    await cmd_tasks(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "Нет открытых задач" in text


@pytest.mark.asyncio
async def test_tasks_group_with_tasks(
    group_chat, telegram_user, now_dt,
    tasks_with_assignees,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_chat = MagicMock()
    mock_chat.title = "Test Group"
    mock_db_user = MagicMock()
    mock_get_chat_context_tasks.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_tasks, tasks_with_assignees)

    await cmd_tasks(msg)

    assert msg.answer.call_count == 3
    texts = [
        call.args[0] if call.args else call.kwargs.get("text", "")
        for call in msg.answer.call_args_list
    ]

    assert "📋 Задачи чата Test Group" in texts[0]
    assert "Сделать отчёт" in texts[1]
    assert "Подготовить презентацию" in texts[2]


@pytest.mark.asyncio
async def test_tasks_no_assignees_shown(
    group_chat, telegram_user, now_dt,
    tasks_no_assignees,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_chat = MagicMock()
    mock_chat.title = "Test Group"
    mock_db_user = MagicMock()
    mock_get_chat_context_tasks.return_value = (mock_chat, None, mock_db_user)
    _setup_sync_mock(mock_sync_tasks, tasks_no_assignees)

    await cmd_tasks(msg)

    assert msg.answer.call_count == 2
    texts = [
        call.args[0] if call.args else call.kwargs.get("text", "")
        for call in msg.answer.call_args_list
    ]

    assert "Общая задача" in texts[1]
    assert "не назначен" in texts[1]


@pytest.mark.asyncio
async def test_tasks_no_user(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(), None, None)

    await cmd_tasks(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0] if msg.answer.call_args[0] else ""
    assert "Не удалось определить пользователя" in text


@pytest.mark.asyncio
async def test_tasks_due_date_formatted(
    private_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    task = _make_mock_task(
        "dated", "Задача со сроком",
        task_id=10,
        due_date=datetime(2026, 5, 15, 12, 0, 0, tzinfo=dt_timezone.utc),
    )

    msg = make_message(private_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task])

    await cmd_tasks(msg)

    assert msg.answer.call_count == 2
    text = msg.answer.call_args_list[1].args[0] if msg.answer.call_args_list[1].args else ""
    assert "📅 до" in text
    assert "15.05.2026" in text


@pytest.mark.asyncio
async def test_tasks_no_due_date(
    private_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    task = _make_mock_task("nodate", "Задача без срока", task_id=11)

    msg = make_message(private_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task])

    await cmd_tasks(msg)

    assert msg.answer.call_count == 2
    text = msg.answer.call_args_list[1].args[0] if msg.answer.call_args_list[1].args else ""
    assert "без срока" in text


# ──────────────────────────────────────────────────────────────────────
# callback: task_done
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_done_success():
    from bot.handlers.tasks import callback_task_done

    callback = AsyncMock()
    callback.data = "task_done:42"
    callback.from_user = MagicMock(id=999)
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    mock_user = MagicMock()

    with patch(
        "bot.handlers.tasks.sync_to_async",
        return_value=AsyncMock(return_value=mock_user),
    ), patch(
        "bot.handlers.tasks.task_service.mark_task_done",
        new=AsyncMock(return_value=True),
    ):
        await callback_task_done(callback)

    callback.answer.assert_called_once()
    assert "выполнена" in callback.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_task_done_failure():
    from bot.handlers.tasks import callback_task_done

    callback = AsyncMock()
    callback.data = "task_done:42"
    callback.from_user = MagicMock(id=999)
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    mock_user = MagicMock()

    with patch(
        "bot.handlers.tasks.sync_to_async",
        return_value=AsyncMock(return_value=mock_user),
    ), patch(
        "bot.handlers.tasks.task_service.mark_task_done",
        new=AsyncMock(return_value=False),
    ):
        await callback_task_done(callback)

    callback.answer.assert_called_once()
    assert "ошибка" in callback.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_task_done_user_not_found():
    from bot.handlers.tasks import callback_task_done

    callback = AsyncMock()
    callback.data = "task_done:42"
    callback.from_user = MagicMock(id=999)
    callback.answer = AsyncMock()

    with patch(
        "bot.handlers.tasks.sync_to_async",
        return_value=AsyncMock(return_value=None),
    ):
        await callback_task_done(callback)

    callback.answer.assert_called_once()
    assert "не найден" in callback.answer.call_args[0][0].lower()

# ──────────────────────────────────────────────────────────────────
# Жёсткие тесты
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tasks_very_long_title(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    long_title = "А" * 1000
    task = _make_mock_task("long", long_title, task_id=1)

    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task])

    try:
        await cmd_tasks(msg)
    except Exception:
        pytest.fail("Не должно падать на длинном title")


@pytest.mark.asyncio
async def test_tasks_special_chars_in_title(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    task = _make_mock_task(
        "xss", "<script>alert('xss')</script> & <b>test</b>", task_id=1
    )

    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task])

    try:
        await cmd_tasks(msg)
    except Exception:
        pytest.fail("HTML-спецсимволы не должны ломать бота")


@pytest.mark.asyncio
async def test_tasks_many_assignees(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    users = [_make_mock_user(username=f"user{i}", user_id=i) for i in range(10)]
    task = _make_mock_task("mass", "Массовая задача", task_id=1, assignees=users)

    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task])

    await cmd_tasks(msg)

    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    combined = " ".join(texts)
    assert "@user0" in combined
    assert "@user9" in combined


@pytest.mark.asyncio
async def test_tasks_assignee_no_username_no_fullname(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    user = MagicMock()
    user.id = 42
    user.username = None
    user.full_name = None
    task = _make_mock_task("noname", "Задача", task_id=1, assignees=[user])

    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task])

    try:
        await cmd_tasks(msg)
    except Exception:
        pytest.fail("Не должно падать если у assignee нет username и full_name")


@pytest.mark.asyncio
async def test_tasks_due_date_in_past(
    private_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    past_date = datetime.now(dt_timezone.utc) - timedelta(days=30)
    task = _make_mock_task("old", "Старая задача", task_id=1, due_date=past_date)

    msg = make_message(private_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task])

    await cmd_tasks(msg)

    texts = [c.args[0] if c.args else "" for c in msg.answer.call_args_list]
    combined = " ".join(texts)
    assert "Старая задача" in combined


@pytest.mark.asyncio
async def test_tasks_empty_title_skipped(
    group_chat, telegram_user, now_dt,
    mock_get_chat_context_tasks, mock_sync_tasks,
):
    from bot.handlers.tasks import cmd_tasks

    task_ok = _make_mock_task("ok", "Нормальная задача", task_id=1)
    task_empty = _make_mock_task("empty", "", task_id=2)

    msg = make_message(group_chat, telegram_user, "/tasks", now_dt)
    mock_get_chat_context_tasks.return_value = (MagicMock(title="Chat"), None, MagicMock())
    _setup_sync_mock(mock_sync_tasks, [task_ok, task_empty])

    await cmd_tasks(msg)
    assert msg.answer.call_count >= 2


@pytest.mark.asyncio
async def test_task_done_invalid_callback_data():
    from bot.handlers.tasks import callback_task_done

    callback = AsyncMock()
    callback.data = "task_done:abc"
    callback.answer = AsyncMock()

    await callback_task_done(callback)

    callback.answer.assert_called_once()
    assert "некорректн" in callback.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_task_done_empty_callback_data():
    from bot.handlers.tasks import callback_task_done

    callback = AsyncMock()
    callback.data = "task_done:"
    callback.answer = AsyncMock()

    await callback_task_done(callback)

    callback.answer.assert_called_once()