"""
Redis-буфер сообщений для батч-обработки.

Каждый чат+топик имеет свой буфер.
Сообщения накапливаются и отправляются пачкой в LLM,
когда набрался MAX_BATCH_SIZE или прошло FLUSH_TIMEOUT_SEC
с момента первого сообщения в текущем батче.
"""

import json
import time
import logging
from typing import Optional

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 5
FLUSH_TIMEOUT_SEC = 30  # секунд ожидания перед принудительным flush

# Префиксы ключей Redis
_KEY_PREFIX = "msg_buf"
_ACTIVE_SET = f"{_KEY_PREFIX}:active"


def _buffer_key(chat_id: int, topic_id: int) -> str:
    return f"{_KEY_PREFIX}:{chat_id}:{topic_id}"


def _ts_key(chat_id: int, topic_id: int) -> str:
    """Ключ для хранения timestamp первого сообщения в текущем батче."""
    return f"{_KEY_PREFIX}:ts:{chat_id}:{topic_id}"


def _get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


class MessageBuffer:
    """
    Потоко-безопасный буфер на базе Redis List.

    Формат элемента (JSON-строка):
    {
        "message_id": int,      # PK в Django
        "text": str,
        "author_name": str,
        "timestamp": float,     # unix
    }
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._r = redis_client or _get_redis()

    # ── public API ───────────────────────────────────────────────

    def add_message(
        self,
        chat_id: int,
        topic_id: int,
        message_data: dict,
    ) -> int:
        """
        Добавляет сообщение в буфер.
        Возвращает текущий размер буфера после добавления.
        """
        key = _buffer_key(chat_id, topic_id)
        ts_key = _ts_key(chat_id, topic_id)

        pipe = self._r.pipeline(transaction=True)
        pipe.rpush(key, json.dumps(message_data, ensure_ascii=False))
        pipe.setnx(ts_key, str(time.time()))  # ставим ts только если ключа нет
        pipe.sadd(_ACTIVE_SET, f"{chat_id}:{topic_id}")
        pipe.llen(key)
        results = pipe.execute()

        current_size: int = results[-1]
        logger.debug(
            "Buffer %s:%s size=%s after add", chat_id, topic_id, current_size
        )
        return current_size

    def should_flush(self, chat_id: int, topic_id: int) -> bool:
        """
        Возвращает True, если буфер пора отправлять:
        — набрался MAX_BATCH_SIZE, или
        — прошло FLUSH_TIMEOUT_SEC с первого сообщения.
        """
        key = _buffer_key(chat_id, topic_id)
        ts_key = _ts_key(chat_id, topic_id)

        size = self._r.llen(key)
        if size == 0:
            return False
        if size >= MAX_BATCH_SIZE:
            return True

        first_ts = self._r.get(ts_key)
        if first_ts is None:
            return False

        elapsed = time.time() - float(first_ts)
        return elapsed >= FLUSH_TIMEOUT_SEC

    def flush(self, chat_id: int, topic_id: int) -> list[dict]:
        """
        Атомарно забирает все сообщения из буфера и очищает его.
        Возвращает список dict'ов.
        """
        key = _buffer_key(chat_id, topic_id)
        ts_key = _ts_key(chat_id, topic_id)

        pipe = self._r.pipeline(transaction=True)
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        pipe.delete(ts_key)
        pipe.srem(_ACTIVE_SET, f"{chat_id}:{topic_id}")
        results = pipe.execute()

        raw_items: list[str] = results[0]
        messages = []
        for raw in raw_items:
            try:
                messages.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.warning("Corrupted buffer entry: %s", raw[:200])
        return messages

    def get_active_buffers(self) -> list[dict]:
        """
        Возвращает список всех активных буферов
        [{"chat_id": int, "topic_id": int}, ...].
        """
        members = self._r.smembers(_ACTIVE_SET)
        result = []
        for member in members:
            try:
                chat_id_str, topic_id_str = member.split(":", 1)
                result.append(
                    {"chat_id": int(chat_id_str), "topic_id": int(topic_id_str)}
                )
            except (ValueError, AttributeError):
                logger.warning("Bad active-set member: %s", member)
        return result

    def peek_size(self, chat_id: int, topic_id: int) -> int:
        """Текущий размер буфера (без извлечения)."""
        return self._r.llen(_buffer_key(chat_id, topic_id))