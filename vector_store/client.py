import logging
from typing import List, Dict, Any, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    Range,
    PayloadSchemaType,
)
from django.conf import settings

logger = logging.getLogger(__name__)


class VectorStoreClient:
    """Клиент для работы с Qdrant"""

    COLLECTION_NAME = "telegram_messages"

    def __init__(self):
        self.client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY
        )
        self._ensure_collection()

    def _ensure_collection(self):
        """Создаёт коллекцию, если её нет"""
        collections = self.client.get_collections().collections
        exists = any(c.name == self.COLLECTION_NAME for c in collections)
        if not exists:
            # Размерность вектора (1536 для text-embedding-ada-002)
            vector_size = 1536
            self.client.create_collection(
                collection_name=self.COLLECTION_NAME,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                on_disk_payload=True
            )
            # Создаём индексы для фильтрации
            self.client.create_payload_index(
                collection_name=self.COLLECTION_NAME,
                field_name="chat_id",
                field_schema=PayloadSchemaType.INTEGER
            )
            self.client.create_payload_index(
                collection_name=self.COLLECTION_NAME,
                field_name="topic_id",
                field_schema=PayloadSchemaType.INTEGER
            )
            self.client.create_payload_index(
                collection_name=self.COLLECTION_NAME,
                field_name="timestamp",
                field_schema=PayloadSchemaType.INTEGER
            )
            logger.info(f"Created collection '{self.COLLECTION_NAME}' with indexes")

    async def upsert_message(
            self,
            message_id: int,
            embedding: List[float],
            payload: Dict[str, Any]
    ):
        """Добавляет или обновляет вектор сообщения"""
        point = PointStruct(
            id=message_id,
            vector=embedding,
            payload=payload
        )
        self.client.upsert(
            collection_name=self.COLLECTION_NAME,
            points=[point]
        )
        logger.debug(f"Upserted message {message_id} to Qdrant")

    async def search_similar(
            self,
            query_embedding: List[float],
            chat_id: int,
            topic_id: Optional[int] = None,
            limit: int = 10,
            time_range_days: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Ищет похожие сообщения с фильтрацией"""
        must_conditions = [
            FieldCondition(key="chat_id", match=MatchValue(value=chat_id))
        ]
        if topic_id is not None:
            must_conditions.append(
                FieldCondition(key="topic_id", match=MatchValue(value=topic_id))
            )
        if time_range_days is not None:
            from datetime import datetime, timedelta
            from_time = int((datetime.now() - timedelta(days=time_range_days)).timestamp())
            must_conditions.append(
                FieldCondition(key="timestamp", range=Range(gte=from_time))
            )

        search_filter = Filter(must=must_conditions) if must_conditions else None

        results = self.client.search(
            collection_name=self.COLLECTION_NAME,
            query_vector=query_embedding,
            limit=limit,
            with_payload=True,
            query_filter=search_filter
        )

        return [
            {
                "id": hit.id,
                "score": hit.score,
                "payload": hit.payload
            }
            for hit in results
        ]