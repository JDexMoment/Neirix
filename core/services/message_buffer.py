import json
import logging
import time
from typing import List, Dict, Optional

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# Ключ в Redis: chat:{chat_id}:topic:{topic_id}:buffer
BUFFER_KEY_TEMPLATE = "msg_buffer:{chat_id}:{topic_id}"
# Ключ для lock'а: не обрабатывать буфер пока уже идёт обработка
LOCK_KEY_TEMPLATE = "msg_buffer_lock:{chat_id}:{topic_id}"

# Настройки батчинга
BATCH_WINDOW_SECONDS = 30       # окно накопления
MAX_BATCH_SIZE = 5              # максимум сообщений в пачке
LOCK_TTL_SECONDS = 120          # таймаут lock'а


class MessageBuffer:
    """
    Буферизация сообщений в Redis для батчинг-анализа.
    
    Поток:
    1. Новое сообщение → add_message() → сохраняется в Redis list
    2. Celery каждые 10 сек проверяет буферы → flush_if_ready()
    3. Если буфер готов (прошло BATCH_WINDOW_SECONDS или >= MAX_BATCH_SIZE) → забираем пачку
    4. Пачка отправляется в LLM одним запросом
    """

    def __init__(self):
        self._redis: Optional[redis.Redis] = None

    @property
    def redis_client(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.Redis(
                host=getattr(settings, "REDIS_HOST", "localhost"),
                port=getattr(settings, "REDIS_PORT", 6379),
                db=getattr(settings, "REDIS_DB", 1),
                decode_responses=True,
            )
        return self._redis

    def _buffer_key(self, chat_id: int, topic_id: int) -> str:
        return BUFFER_KEY_TEMPLATE.format(chat_id=chat_id, topic_id=topic_id)

    def _lock_key(self, chat_id: int, topic_id: int) -> str:
        return LOCK_KEY_TEMPLATE.format(chat_id=chat_id, topic_id=topic_id)

    def add_message(self, chat_id: int, topic_id: int, message_data: Dict) -> int:
        """
        Добавляет сообщение в буфер. Возвращает текущий размер буфера.
        
        message_data должен содержать:
        - message_id: int
        - text: str
        - author_name: str
        - timestamp: float (unix timestamp)
        """
        key = self._buffer_key(chat_id, topic_id)

        entry = {
            "message_id": message_data["message_id"],
            "text": message_data["text"],
            "author_name": message_data["author_name"],
            "timestamp": message_data.get("timestamp", time.time()),
        }

        pipe = self.redis_client.pipeline()
        pipe.rpush(key, json.dumps(entry, ensure_ascii=False))
        # Автоудаление через 5 минут если никто не забрал
        pipe.expire(key, 300)
        results = pipe.execute()

        buffer_size = results[0]
        logger.debug(
            "Message buffered | chat=%s topic=%s size=%s",
            chat_id, topic_id, buffer_size,
        )
        return buffer_size

    def should_flush(self, chat_id: int, topic_id: int) -> bool:
        """
        Проверяет, нужно ли сейчас забирать буфер:
        - если >= MAX_BATCH_SIZE сообщений
        - или если самое старое сообщение старше BATCH_WINDOW_SECONDS
        """
        key = self._buffer_key(chat_id, topic_id)

        size = self.redis_client.llen(key)
        if size == 0:
            return False

        if size >= MAX_BATCH_SIZE:
            return True

        # Проверяем возраст самого старого сообщения
        first_raw = self.redis_client.lindex(key, 0)
        if first_raw:
            try:
                first = json.loads(first_raw)
                age = time.time() - first.get("timestamp", time.time())
                if age >= BATCH_WINDOW_SECONDS:
                    return True
            except (json.JSONDecodeError, KeyError):
                return True

        return False

    def flush(self, chat_id: int, topic_id: int) -> List[Dict]:
        """
        Атомарно забирает все сообщения из буфера.
        Использует lock чтобы два worker'а не забрали одно и то же.
        Возвращает список сообщений или пустой список.
        """
        lock_key = self._lock_key(chat_id, topic_id)
        buffer_key = self._buffer_key(chat_id, topic_id)

        # Пытаемся взять lock
        acquired = self.redis_client.set(
            lock_key, "1", nx=True, ex=LOCK_TTL_SECONDS,
        )
        if not acquired:
            return []

        try:
            # Забираем все сообщения атомарно
            pipe = self.redis_client.pipeline()
            pipe.lrange(buffer_key, 0, -1)
            pipe.delete(buffer_key)
            results = pipe.execute()

            raw_messages = results[0]
            messages = []
            for raw in raw_messages:
                try:
                    messages.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue

            logger.info(
                "Buffer flushed | chat=%s topic=%s count=%s",
                chat_id, topic_id, len(messages),
            )
            return messages

        finally:
            self.redis_client.delete(lock_key)

    def get_active_buffers(self) -> List[Dict[str, int]]:
        """
        Возвращает список всех активных буферов (chat_id, topic_id).
        """
        pattern = BUFFER_KEY_TEMPLATE.format(chat_id="*", topic_id="*")
        keys = self.redis_client.keys(pattern)

        buffers = []
        for key in keys:
            parts = key.split(":")
            if len(parts) >= 4:
                try:
                    buffers.append({
                        "chat_id": int(parts[1]),
                        "topic_id": int(parts[2]),
                    })
                except (ValueError, IndexError):
                    continue

        return buffers