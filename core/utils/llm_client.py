import json
import logging
from typing import List, Optional, Dict, Any
from openai import AsyncOpenAI
from django.conf import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Асинхронный клиент для OpenAI-совместимого API"""

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE_URL
        )
        self.model = settings.LLM_MODEL_NAME
        self.embedding_model = settings.EMBEDDING_MODEL

    async def chat_completion(
            self,
            messages: List[Dict[str, str]],
            model: Optional[str] = None,
            temperature: float = 0.7,
            max_tokens: int = 2000,
            response_format: Optional[Dict[str, str]] = None
    ) -> str:
        """Отправляет запрос к LLM и возвращает текст ответа"""
        try:
            response = await self.client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format
            )
            content = response.choices[0].message.content
            logger.debug(f"LLM response received, length={len(content)}")
            return content
        except Exception as e:
            logger.error(f"LLM chat completion failed: {e}")
            raise

    async def generate_embedding(self, text: str) -> List[float]:
        """Генерирует эмбеддинг для переданного текста"""
        try:
            # Очищаем текст от лишних пробелов и обрезаем до разумной длины
            text = text.strip()
            if len(text) > 8000:
                text = text[:8000]  # ограничение для многих моделей эмбеддингов

            response = await self.client.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            embedding = response.data[0].embedding
            logger.debug(f"Embedding generated, size={len(embedding)}")
            return embedding
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    async def extract_tasks_from_message(self, message_text: str) -> List[Dict[str, Any]]:
        """Извлекает задачи из текста сообщения через LLM"""
        prompt = f"""Проанализируй следующее сообщение из рабочего чата.
Извлеки все задачи, которые были поставлены.
Для каждой задачи укажи:
- Название задачи
- Ответственного (username или имя, если упомянут)
- Дедлайн (если указан, в формате YYYY-MM-DD)
- Описание

Если ничего не найдено, верни пустой массив.
Ответ верни в формате JSON:
{{
  "tasks": [
    {{
      "title": "...",
      "assignee": "...",
      "due_date": "YYYY-MM-DD",
      "description": "..."
    }}
  ]
}}

Сообщение:
{message_text}"""

        messages = [
            {"role": "system", "content": "Ты — помощник для извлечения задач из текста."},
            {"role": "user", "content": prompt}
        ]

        try:
            response = await self.chat_completion(
                messages=messages,
                temperature=0.2,
                max_tokens=1000,
                response_format={"type": "json_object"}
            )
            data = json.loads(response)
            return data.get("tasks", [])
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON from LLM task extraction")
            return []
        except Exception as e:
            logger.error(f"Task extraction failed: {e}")
            return []

    async def extract_meeting_from_message(self, message_text: str) -> Optional[Dict[str, Any]]:
        """Извлекает информацию о встрече из сообщения"""
        prompt = f"""Проанализируй сообщение и определи, содержится ли в нём информация о запланированной встрече/созвоне.
Если да, извлеки:
- Название встречи
- Дату и время начала (в формате ISO 8601: YYYY-MM-DDTHH:MM:SS)
- Участников (список имён или username)

Если встречи нет, верни null.
Ответ верни в формате JSON:
{{
  "meeting": {{
    "title": "...",
    "start_at": "2025-01-15T14:30:00",
    "participants": ["имя1", "имя2"]
  }}
}} или {{ "meeting": null }}

Сообщение:
{message_text}"""

        messages = [
            {"role": "system", "content": "Ты — помощник для извлечения информации о встречах."},
            {"role": "user", "content": prompt}
        ]

        try:
            response = await self.chat_completion(
                messages=messages,
                temperature=0.2,
                max_tokens=800,
                response_format={"type": "json_object"}
            )
            data = json.loads(response)
            return data.get("meeting")
        except Exception as e:
            logger.error(f"Meeting extraction failed: {e}")
            return None

    async def generate_summary(
            self,
            messages_context: str,
            tasks_context: str = "",
            meetings_context: str = ""
    ) -> str:
        """Генерирует структурированное саммари рабочей переписки"""
        prompt = f"""Ты — помощник для создания саммари рабочих обсуждений.
Проанализируй переписку за указанный период и создай структурированное саммари.

Включи в саммари:
1. Основные обсуждаемые темы
2. Принятые решения
3. Поставленные задачи и ответственных (если есть)
4. Запланированные встречи (если есть)
5. Нерешенные вопросы

Форматируй ответ красиво, используй списки и выделение главного.
Пиши на русском языке.

Контекст переписки:
{messages_context}

Дополнительная информация о задачах:
{tasks_context if tasks_context else "Нет данных"}

Информация о встречах:
{meetings_context if meetings_context else "Нет данных"}"""

        messages = [
            {"role": "system", "content": "Ты — профессиональный ассистент для создания итогов встреч."},
            {"role": "user", "content": prompt}
        ]

        return await self.chat_completion(
            messages=messages,
            temperature=0.5,
            max_tokens=2000
        )