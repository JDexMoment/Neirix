"""
Тесты для MessageBuffer (Redis-буфер сообщений).
"""

import json
import time
import pytest
from unittest.mock import MagicMock, patch, call


@pytest.fixture
def mock_redis():
    """Мок Redis-клиента."""
    r = MagicMock()
    r.pipeline.return_value = r  # pipe = r.pipeline() → pipe is r
    r.execute.return_value = [None, True, 1, 3]  # defaults
    r.llen.return_value = 0
    r.get.return_value = None
    r.smembers.return_value = set()
    r.lrange.return_value = []
    return r


@pytest.fixture
def buffer(mock_redis):
    from core.services.message_buffer import MessageBuffer

    return MessageBuffer(redis_client=mock_redis)


class TestMessageBuffer:

    def test_add_message_returns_size(self, buffer, mock_redis):
        """add_message возвращает текущий размер буфера."""
        mock_redis.execute.return_value = [1, True, 1, 3]

        size = buffer.add_message(
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
        mock_redis.rpush.assert_called_once()

    def test_add_message_sets_timestamp_only_once(self, buffer, mock_redis):
        """setnx вызывается — timestamp ставится только при первом сообщении."""
        mock_redis.execute.return_value = [1, True, 1, 1]

        buffer.add_message(
            chat_id=-100,
            topic_id=0,
            message_data={"message_id": 1, "text": "a", "author_name": "u", "timestamp": 1.0},
        )

        mock_redis.setnx.assert_called_once()

    def test_should_flush_empty_buffer(self, buffer, mock_redis):
        """Пустой буфер → should_flush = False."""
        mock_redis.llen.return_value = 0

        assert buffer.should_flush(-100, 0) is False

    def test_should_flush_full_batch(self, buffer, mock_redis):
        """Полный батч → should_flush = True."""
        from core.services.message_buffer import MAX_BATCH_SIZE

        mock_redis.llen.return_value = MAX_BATCH_SIZE

        assert buffer.should_flush(-100, 0) is True

    def test_should_flush_timeout(self, buffer, mock_redis):
        """Таймаут прошёл → should_flush = True."""
        from core.services.message_buffer import FLUSH_TIMEOUT_SEC

        mock_redis.llen.return_value = 1
        mock_redis.get.return_value = str(time.time() - FLUSH_TIMEOUT_SEC - 1)

        assert buffer.should_flush(-100, 0) is True

    def test_should_flush_not_yet(self, buffer, mock_redis):
        """Буфер не полный и таймаут не прошёл → should_flush = False."""
        mock_redis.llen.return_value = 2
        mock_redis.get.return_value = str(time.time())

        assert buffer.should_flush(-100, 0) is False

    def test_flush_returns_messages(self, buffer, mock_redis):
        """flush возвращает список dict'ов и очищает буфер."""
        raw_messages = [
            json.dumps({"message_id": 1, "text": "hello", "author_name": "u", "timestamp": 1.0}),
            json.dumps({"message_id": 2, "text": "world", "author_name": "v", "timestamp": 2.0}),
        ]
        mock_redis.execute.return_value = [raw_messages, 1, 1, 1]

        result = buffer.flush(-100, 0)

        assert len(result) == 2
        assert result[0]["message_id"] == 1
        assert result[1]["text"] == "world"
        mock_redis.delete.assert_called()

    def test_flush_empty_buffer(self, buffer, mock_redis):
        """flush пустого буфера → пустой список."""
        mock_redis.execute.return_value = [[], 0, 0, 0]

        result = buffer.flush(-100, 0)

        assert result == []

    def test_flush_corrupted_json_skipped(self, buffer, mock_redis):
        """Испорченная запись в буфере → пропускается."""
        raw_messages = [
            json.dumps({"message_id": 1, "text": "ok", "author_name": "u", "timestamp": 1.0}),
            "not-a-json{{{",
            json.dumps({"message_id": 3, "text": "fine", "author_name": "v", "timestamp": 3.0}),
        ]
        mock_redis.execute.return_value = [raw_messages, 1, 1, 1]

        result = buffer.flush(-100, 0)

        assert len(result) == 2
        assert result[0]["message_id"] == 1
        assert result[1]["message_id"] == 3

    def test_get_active_buffers(self, buffer, mock_redis):
        """get_active_buffers парсит ключи из Redis set."""
        mock_redis.smembers.return_value = {"-100:0", "-200:5"}

        result = buffer.get_active_buffers()

        assert len(result) == 2
        chat_ids = {r["chat_id"] for r in result}
        assert chat_ids == {-100, -200}

    def test_get_active_buffers_bad_member_skipped(self, buffer, mock_redis):
        """Невалидная запись в active set → пропускается."""
        mock_redis.smembers.return_value = {"-100:0", "bad_data", "-200:5"}

        result = buffer.get_active_buffers()

        assert len(result) == 2

    def test_peek_size(self, buffer, mock_redis):
        """peek_size возвращает llen."""
        mock_redis.llen.return_value = 3

        assert buffer.peek_size(-100, 0) == 3