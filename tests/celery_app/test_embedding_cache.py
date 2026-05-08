"""
Тесты для кэширования embeddings (vector_store/embeddings.py).
"""

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.get.return_value = None
    r.mget.return_value = []
    r.setex = MagicMock()
    r.pipeline.return_value = r
    r.execute.return_value = []
    return r


@pytest.fixture
def mock_model():
    import numpy as np

    model = MagicMock()
    model.encode.return_value = np.array([0.1, 0.2, 0.3])
    return model


class TestEmbeddingCache:

    @pytest.mark.asyncio
    async def test_cache_miss_computes_and_stores(self, mock_redis, mock_model):
        """Кэш пуст → вычисляет embedding и сохраняет."""
        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            with patch("vector_store.embeddings._get_model", return_value=mock_model):
                from vector_store.embeddings import generate_embedding

                result = await generate_embedding("hello world")

        assert result == [0.1, 0.2, 0.3]
        mock_model.encode.assert_called_once_with("hello world", normalize_embeddings=True)
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_computation(self, mock_redis, mock_model):
        """Кэш есть → возвращает из кэша, модель не вызывается."""
        mock_redis.get.return_value = json.dumps([0.4, 0.5, 0.6])

        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            with patch("vector_store.embeddings._get_model", return_value=mock_model):
                from vector_store.embeddings import generate_embedding

                result = await generate_embedding("hello world")

        assert result == [0.4, 0.5, 0.6]
        mock_model.encode.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self, mock_redis):
        """Пустой текст → None."""
        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            from vector_store.embeddings import generate_embedding

            assert await generate_embedding("") is None
            assert await generate_embedding("   ") is None

    @pytest.mark.asyncio
    async def test_batch_all_cached(self, mock_redis, mock_model):
        """Все тексты в кэше → модель не вызывается."""
        cached = [
            json.dumps([0.1, 0.2]),
            json.dumps([0.3, 0.4]),
        ]
        mock_redis.mget.return_value = cached

        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            with patch("vector_store.embeddings._get_model", return_value=mock_model):
                from vector_store.embeddings import generate_embeddings_batch

                result = await generate_embeddings_batch(["a", "b"])

        assert len(result) == 2
        assert result[0] == [0.1, 0.2]
        assert result[1] == [0.3, 0.4]
        mock_model.encode.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_partial_cache(self, mock_redis, mock_model):
        """Часть в кэше, часть нет → вычисляем только отсутствующие."""
        import numpy as np

        # Первый текст в кэше, второй нет
        mock_redis.mget.return_value = [
            json.dumps([0.1, 0.2]),
            None,
        ]
        mock_model.encode.return_value = np.array([[0.5, 0.6]])

        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            with patch("vector_store.embeddings._get_model", return_value=mock_model):
                from vector_store.embeddings import generate_embeddings_batch

                result = await generate_embeddings_batch(["cached_text", "new_text"])

        assert result[0] == [0.1, 0.2]  # из кэша
        assert result[1] == [0.5, 0.6]  # вычислено
        # encode вызван только с ["new_text"]
        mock_model.encode.assert_called_once()
        encode_args = mock_model.encode.call_args
        assert encode_args[0][0] == ["new_text"]

    @pytest.mark.asyncio
    async def test_batch_none_cached(self, mock_redis, mock_model):
        """Ничего в кэше → всё вычисляется."""
        import numpy as np

        mock_redis.mget.return_value = [None, None, None]
        mock_model.encode.return_value = np.array([
            [0.1, 0.2],
            [0.3, 0.4],
            [0.5, 0.6],
        ])

        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            with patch("vector_store.embeddings._get_model", return_value=mock_model):
                from vector_store.embeddings import generate_embeddings_batch

                result = await generate_embeddings_batch(["a", "b", "c"])

        assert len(result) == 3
        mock_model.encode.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_empty_list(self, mock_redis):
        """Пустой список → пустой результат."""
        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            from vector_store.embeddings import generate_embeddings_batch

            result = await generate_embeddings_batch([])

        assert result == []

    @pytest.mark.asyncio
    async def test_same_text_same_cache_key(self, mock_redis):
        """Одинаковый текст → одинаковый ключ кэша."""
        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            from vector_store.embeddings import _cache_key

            key1 = _cache_key("hello")
            key2 = _cache_key("hello")
            key3 = _cache_key("world")

            assert key1 == key2
            assert key1 != key3

    @pytest.mark.asyncio
    async def test_redis_setex_failure_does_not_crash(self, mock_redis, mock_model):
        """Ошибка Redis при записи кэша → не крашит."""
        mock_redis.setex.side_effect = Exception("Redis down")

        with patch("vector_store.embeddings._get_redis", return_value=mock_redis):
            with patch("vector_store.embeddings._get_model", return_value=mock_model):
                from vector_store.embeddings import generate_embedding

                result = await generate_embedding("test")

        # Embedding всё равно вернулся
        assert result == [0.1, 0.2, 0.3]