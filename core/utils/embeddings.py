import asyncio
from typing import List
from django.core.cache import cache
from .llm_client import LLMClient

llm_client = LLMClient()


async def generate_embedding(text: str) -> List[float]:
    """Генерирует эмбеддинг для текста с кэшированием"""
    # Простой хеш для ключа кэша (можно улучшить)
    cache_key = f"emb:{hash(text)}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    embedding = await llm_client.generate_embedding(text)
    # Кэшируем на сутки
    cache.set(cache_key, embedding, timeout=86400)
    return embedding