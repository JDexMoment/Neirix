"""
Генерация и кэширование embeddings.

Кэш: Redis, ключ = sha256(text), TTL = 24 часа.
При batch-запросе сначала проверяем кэш для каждого текста,
вычисляем только отсутствующие, затем сохраняем новые в кэш.
"""

import hashlib
import json
import logging
from typing import Optional

import numpy as np
import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# ── Ленивая загрузка модели ──────────────────────────────────────
_model = None

CACHE_TTL = 60 * 60 * 24  # 24 часа
_CACHE_PREFIX = "emb_cache"


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        model_name = getattr(
            settings,
            "EMBEDDING_MODEL_NAME",
            "sentence-transformers/all-MiniLM-L6-v2",
        )
        _model = SentenceTransformer(model_name)
        logger.info("SentenceTransformer loaded: %s", model_name)
    return _model


def _get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def _cache_key(text: str) -> str:
    """Детерминированный ключ кэша по содержимому текста."""
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{_CACHE_PREFIX}:{text_hash}"


def _serialize_embedding(emb: list[float]) -> str:
    return json.dumps(emb)


def _deserialize_embedding(raw: str) -> list[float]:
    return json.loads(raw)


# ── Public API ───────────────────────────────────────────────────


async def generate_embedding(text: str) -> Optional[list[float]]:
    """
    Генерирует embedding для одного текста.
    Сначала проверяет Redis-кэш.
    """
    if not text or not text.strip():
        return None

    r = _get_redis()
    key = _cache_key(text)

    # 1. Проверяем кэш
    cached = r.get(key)
    if cached is not None:
        logger.debug("Embedding cache HIT: %s…", text[:40])
        return _deserialize_embedding(cached)

    # 2. Вычисляем
    logger.debug("Embedding cache MISS: %s…", text[:40])
    model = _get_model()
    embedding = model.encode(text, normalize_embeddings=True).tolist()

    # 3. Сохраняем в кэш
    try:
        r.setex(key, CACHE_TTL, _serialize_embedding(embedding))
    except Exception as e:
        logger.warning("Failed to cache embedding: %s", e)

    return embedding


async def generate_embeddings_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """
    Batch-генерация embeddings с кэшированием.

    Для каждого текста:
    - если есть в кэше → берём оттуда
    - если нет → вычисляем пачкой через model.encode()
    - новые embeddings сохраняем в кэш

    Возвращает список той же длины, что texts.
    None для пустых текстов.
    """
    if not texts:
        return []

    r = _get_redis()
    results: list[Optional[list[float]]] = [None] * len(texts)

    # ── 1. Batch-проверка кэша ───────────────────────────────
    cache_keys = []
    for t in texts:
        cache_keys.append(_cache_key(t) if (t and t.strip()) else "")

    # mget для всех непустых ключей
    non_empty_indices = [i for i, k in enumerate(cache_keys) if k]
    non_empty_keys = [cache_keys[i] for i in non_empty_indices]

    cached_values = r.mget(non_empty_keys) if non_empty_keys else []

    # Раскладываем кэшированные значения
    to_compute_indices: list[int] = []  # индексы в texts, которые нужно вычислить

    for idx_in_batch, idx_in_texts in enumerate(non_empty_indices):
        raw = cached_values[idx_in_batch]
        if raw is not None:
            results[idx_in_texts] = _deserialize_embedding(raw)
        else:
            to_compute_indices.append(idx_in_texts)

    cache_hits = len(non_empty_indices) - len(to_compute_indices)
    if cache_hits > 0:
        logger.debug("Embedding batch: %s hits, %s misses", cache_hits, len(to_compute_indices))

    # ── 2. Вычисляем отсутствующие ───────────────────────────
    if to_compute_indices:
        compute_texts = [texts[i] for i in to_compute_indices]
        model = _get_model()
        embeddings_np = model.encode(
            compute_texts,
            normalize_embeddings=True,
            batch_size=min(len(compute_texts), 64),
            show_progress_bar=False,
        )

        # ── 3. Сохраняем в кэш и результат ──────────────────
        pipe = r.pipeline(transaction=False)
        for local_idx, text_idx in enumerate(to_compute_indices):
            emb = embeddings_np[local_idx].tolist()
            results[text_idx] = emb
            try:
                pipe.setex(
                    cache_keys[text_idx],
                    CACHE_TTL,
                    _serialize_embedding(emb),
                )
            except Exception as e:
                logger.warning("Failed to cache embedding idx=%s: %s", text_idx, e)

        try:
            pipe.execute()
        except Exception as e:
            logger.warning("Redis pipe execute failed: %s", e)

    return results