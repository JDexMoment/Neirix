import asyncio
import datetime
import json
import logging
from typing import List, Optional, Dict, Any
from gigachat import GigaChat
from gigachat.models import Chat
from sentence_transformers import SentenceTransformer
from django.conf import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Клиент: GigaChat для чата + локальная SentenceTransformer для эмбеддингов"""

    def __init__(self):
        self._chat_client = GigaChat(
            credentials=settings.LLM_API_KEY,
            scope="GIGACHAT_API_PERS",
            model=settings.LLM_MODEL_NAME,
            verify_ssl_certs=False,
        )
        self._embed_model = SentenceTransformer(settings.EMBEDDING_MODEL)

    async def _run_sync(self, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        # Прямая передача списка словарей в Chat
        response = await self._run_sync(
            self._chat_client.chat,
            Chat(messages=messages)
        )
        return response.choices[0].message.content

    async def generate_embedding(self, text: str) -> List[float]:
        embedding = await self._run_sync(self._embed_model.encode, text)
        return embedding.tolist()

    # ---------- Методы для извлечения сущностей ----------
    async def extract_tasks_from_message(self, message_text: str) -> List[Dict[str, Any]]:
        prompt = f"""Проанализируй сообщение из рабочего чата. Извлеки все поставленные задачи.
    Для каждой задачи определи:
    - Название задачи (кратко)
    - Ответственного (если указан через @username или слова "ответственный", "отв.", "assignee", "исполнитель" и т.п.)
    - Дедлайн в формате YYYY-MM-DD (если указан)
    - Описание (если есть)

    Если ответственный не указан, оставь поле assignee пустым.
    Если ничего не найдено, верни пустой список.

    Ответ верни строго в JSON:
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
            {"role": "system", "content": "Ты — помощник для извлечения задач. Возвращай только JSON."},
            {"role": "user", "content": prompt}
        ]
        try:
            response = await self.chat_completion(messages=messages, temperature=0.1)
            # Очищаем возможные markdown-обёртки
            clean = response.strip().replace('```json', '').replace('```', '')
            data = json.loads(clean)
            return data.get("tasks", [])
        except Exception as e:
            logger.error(f"Task extraction failed: {e}")
            return []

    async def extract_meeting_from_message(self, message_text: str, current_context: str) -> Optional[Dict[str, Any]]:
        # current_context содержит текущую дату и время сервера
        prompt = f"""Сегодняшняя дата и время: {current_context}.
    Проанализируй сообщение из рабочего чата. Определи, есть ли в нём информация о встрече/созвоне.
    Если в тексте указано "завтра", "послезавтра", "в среду" и т.д., вычисли точную дату относительно сегодняшней.
    Если точное время не указано, по умолчанию используй 09:00:00.

    Извлеки:
    - Название встречи
    - Дата и время начала (в формате ISO 8601: YYYY-MM-DDTHH:MM:SS)
    - Участники (список имен или @username)

    Если встречи нет, верни null.
    Ответ верни строго в формате JSON:
    {{
    "meeting": {{
        "title": "...",
        "start_at": "YYYY-MM-DDTHH:MM:SS",
        "participants": ["имя1", "@username"]
    }}
    }} или {{ "meeting": null }}

    Сообщение:
    {message_text}"""
    
        messages = [
            {"role": "system", "content": "Ты — помощник для извлечения информации о встречах. Используй текущий контекст времени для вычисления относительных дат."},
            {"role": "user", "content": prompt}
        ]
        try:
            response = await self.chat_completion(messages=messages)
            # Очистка от markdown-блоков, если LLM их добавила
            clean_json = response.strip().replace('```json', '').replace('```', '')
            data = json.loads(clean_json)
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
        return await self.chat_completion(messages=messages)