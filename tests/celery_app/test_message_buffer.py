"""
Тесты для MessageBuffer (Redis-буфер сообщений).
"""

import json
import time
import pytest
from unittest.mock import MagicMock, patch, call
import asyncio


class AwaitableMock(MagicMock):
    def __await__(self):
        async def get_result():
            result = self.return_value
            if hasattr(result, "__await__"):
                return await result
            return result

        return get_result().__await__()


class _Awaitable:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def get_value():
            return self.value

        return get_value().__await__()


@pytest.fixture
def mock_redis():
    """Мок Redis-клиента."""
    r = MagicMock()
    # Создаем pipe_mock заранее
    pipe_mock = MagicMock()
    # Используем AwaitableMock для execute_async
    execute_async_mock = AwaitableMock()
    pipe_mock.execute_async = execute_async_mock
    # Настраиваем r.pipeline так, чтобы он всегда возвращал этот pipe_mock
    r.pipeline.return_value = pipe_mock

    # Для других потенциальных вызовов execute_async на самом r
    r.execute_async = AwaitableMock()

    r.llen = MagicMock()
    r.llen.return_value = _Awaitable(0)
    
    r.get = MagicMock()
    r.get.return_value = _Awaitable(None)
    r.smembers = MagicMock()
    r.smembers.return_value = _Awaitable(set())
    r.lrange = MagicMock()
    r.lrange.return_value = _Awaitable([])
    return r


@pytest.fixture
def buffer(mock_redis):
    from core.services.message_buffer import MessageBuffer

    return MessageBuffer(redis_client=mock_redis)


class TestMessageBuffer:

    @pytest.mark.asyncio
    async def test_add_message_returns_size(self, buffer, mock_redis):
        """add_message возвращает текущий размер буфера."""
        expected_results = [1, True, 1, 3]
        # Оборачиваем в _Awaitable, чтобы await сработал
        mock_redis.pipeline.return_value.execute_async.return_value = _Awaitable(expected_results)

        size = await buffer.add_message(
            chat_id=-100,
            topic_id=0,
            message_data={
                "message_id": 1,
                "text": "test",
                "author_name": "user",
                "timestamp": 1700000000.0,
            },
        )

        assert size == 3
        mock_redis.pipeline.return_value.rpush.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_message_sets_timestamp_only_once(self, buffer, mock_redis):
        """setnx вызывается — timestamp ставится только при первом сообщении."""
        expected_results = [1, True, 1, 1]
        mock_redis.pipeline.return_value.execute_async.return_value = _Awaitable(expected_results)

        await buffer.add_message(
            chat_id=-100,
            topic_id=0,
            message_data={"message_id": 1, "text": "a", "author_name": "u", "timestamp": 1.0},
        )

        mock_redis.pipeline.return_value.setnx.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_flush_empty_buffer(self, buffer, mock_redis):
        """Пустой буфер → should_flush = False."""
        mock_redis.llen.return_value = _Awaitable(0)

        assert await buffer.should_flush(-100, 0) is False

    @pytest.mark.asyncio
    async def test_should_flush_full_batch(self, buffer, mock_redis):
        """Полный батч → should_flush = True."""
        from core.services.message_buffer import MAX_BATCH_SIZE

        mock_redis.llen.return_value = _Awaitable(MAX_BATCH_SIZE)

        assert await buffer.should_flush(-100, 0) is True

    @pytest.mark.asyncio
    async def test_should_flush_timeout(self, buffer, mock_redis):
        """Таймаут прошёл → should_flush = True."""
        from core.services.message_buffer import FLUSH_TIMEOUT_SEC

        mock_redis.llen.return_value = _Awaitable(1)
        mock_redis.get.return_value = _Awaitable(str(time.time() - FLUSH_TIMEOUT_SEC - 1))

        assert await buffer.should_flush(-100, 0) is True

    @pytest.mark.asyncio
    async def test_should_flush_not_yet(self, buffer, mock_redis):
        """Буфер не полный и таймаут не прошёл → should_flush = False."""
        mock_redis.llen.return_value = _Awaitable(2)
        mock_redis.get.return_value = _Awaitable(str(time.time()))

        assert await buffer.should_flush(-100, 0) is False

    @pytest.mark.asyncio
    async def test_flush_returns_messages(self, buffer, mock_redis):
        """flush возвращает список dict'ов и очищает буфер."""
        raw_messages = [
            json.dumps({"message_id": 1, "text": "hello", "author_name": "u", "timestamp": 1.0}),
            json.dumps({"message_id": 2, "text": "world", "author_name": "v", "timestamp": 2.0}),
        ]
        expected_results = [raw_messages, 1, 1, 1]
        mock_redis.pipeline.return_value.execute_async.return_value = _Awaitable(expected_results)

        result = await buffer.flush(-100, 0)

        assert len(result) == 2
        assert result[0]["message_id"] == 1
        assert result[1]["text"] == "world"
        mock_redis.pipeline.return_value.delete.assert_called()

    @pytest.mark.asyncio
    async def test_flush_empty_buffer(self, buffer, mock_redis):
        """flush пустого буфера → пустой список."""
        expected_results = [[], 0, 0, 0]
        mock_redis.pipeline.return_value.execute_async.return_value = _Awaitable(expected_results)

        result = await buffer.flush(-100, 0)

        assert result == []

    @pytest.mark.asyncio
    async def test_flush_corrupted_json_skipped(self, buffer, mock_redis):
        """Испорченная запись в буфере → пропускается."""
        raw_messages = [
            json.dumps({"message_id": 1, "text": "ok", "author_name": "u", "timestamp": 1.0}),
            "not-a-json{{{",
            json.dumps({"message_id": 3, "text": "fine", "author_name": "v", "timestamp": 3.0}),
        ]
        expected_results = [raw_messages, 1, 1, 1]
        mock_redis.pipeline.return_value.execute_async.return_value = _Awaitable(expected_results)

        result = await buffer.flush(-100, 0)

        assert len(result) == 2
        assert result[0]["message_id"] == 1
        assert result[1]["message_id"] == 3

    @pytest.mark.asyncio
    async def test_get_active_buffers(self, buffer, mock_redis):
        """get_active_buffers парсит ключи из Redis set."""
        mock_redis.smembers.return_value = _Awaitable({"-100:0", "-200:5"})

        result = await buffer.get_active_buffers()

        assert len(result) == 2
        chat_ids = {r["chat_id"] for r in result}
        assert chat_ids == {-100, -200}

    @pytest.mark.asyncio
    async def test_get_active_buffers_bad_member_skipped(self, buffer, mock_redis):
        """Невалидная запись в active set → пропускается."""
        mock_redis.smembers.return_value = _Awaitable({"-100:0", "bad_data", "-200:5"})

        result = await buffer.get_active_buffers()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_peek_size(self, buffer, mock_redis):
        """peek_size возвращает llen."""
        mock_redis.llen.return_value = _Awaitable(3)

        assert await buffer.peek_size(-100, 0) == 3