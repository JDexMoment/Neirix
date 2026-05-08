import asyncio
import calendar as cal_mod
import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Загрузка JSON-конфигов
# ─────────────────────────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_json(filename: str) -> Any:
    path = _PROMPTS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


DAY_FORMS: List[Dict] = _load_json("day_forms.json")
TASK_RULES: Dict = _load_json("task_rules.json")
MEETING_RULES: Dict = _load_json("meeting_rules.json")
SUMMARY_RULES: Dict = _load_json("summary_rules.json")

# ─────────────────────────────────────────────────────────────────────────────
# Константы
# ─────────────────────────────────────────────────────────────────────────────

USERNAME_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{5,32}")

MONTH_NAMES_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

MONTH_WORDS_PATTERN = "|".join(MONTH_NAMES_RU.values())

# ─────────────────────────────────────────────────────────────────────────────
# Утилиты: @username
# ─────────────────────────────────────────────────────────────────────────────


def extract_mentions(text: str) -> List[str]:
    seen: set = set()
    result: List[str] = []
    for username in USERNAME_RE.findall(text):
        low = username.lower()
        if low not in seen:
            seen.add(low)
            result.append(username)
    return result


def _normalize_usernames(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    result: List[str] = []
    seen: set = set()
    for item in value:
        if not isinstance(item, str):
            continue
        found = USERNAME_RE.findall(item)
        if found:
            for username in found:
                low = username.lower()
                if low not in seen:
                    seen.add(low)
                    result.append(username)
        else:
            clean = item.strip()
            if clean and re.match(r"^[A-Za-z0-9_]{1,32}$", clean):
                username = f"@{clean}"
                low = username.lower()
                if low not in seen:
                    seen.add(low)
                    result.append(username)
    return result


def _merge_usernames(from_llm: List[str], from_regex: List[str]) -> List[str]:
    seen: set = set()
    result: List[str] = []
    for username in from_llm + from_regex:
        low = username.lower()
        if low not in seen:
            seen.add(low)
            result.append(username)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Утилиты: даты
# ─────────────────────────────────────────────────────────────────────────────


def _next_weekday_date(base_date, target_weekday: int):
    diff = (target_weekday - base_date.weekday()) % 7
    return base_date + timedelta(days=diff if diff > 0 else 7)


def _normalize_due_date(value: Any) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _normalize_start_at(value: Any) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%d.%m.%YT%H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt in ("%Y-%m-%d", "%d.%m.%Y"):
                dt = dt.replace(hour=9, minute=0, second=0)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None


def _normalize_time(value: Any) -> Optional[str]:
    """Нормализует строку времени в формат HH:MM."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    # Форматы: "11:30", "9:00", "07:00:00"
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            continue
    # Попытка извлечь время из произвольной строки: "в 9", "9 утра"
    m = re.search(r"\b(\d{1,2})\s*(?::(\d{2}))?\s*(утра|вечера|дня)?\b", value)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        period = m.group(3)
        if period == "вечера" and hour < 12:
            hour += 12
        elif period == "дня" and hour < 12:
            hour += 12
        elif period == "утра" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


def _build_start_at_from_date_time(
    date_str: Optional[str],
    time_str: Optional[str],
) -> Optional[str]:
    """
    Собирает start_at (ISO) из отдельных date и time.
    Если нет даты — None.
    Если нет времени — ставит 09:00.
    """
    if not date_str:
        return None
    normalized_time = _normalize_time(time_str) if time_str else None
    if normalized_time:
        return f"{date_str}T{normalized_time}:00"
    return f"{date_str}T09:00:00"


def _contains_explicit_date(text: str) -> bool:
    t = text.lower()
    return bool(
        re.search(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b", t)
        or re.search(rf"\b\d{{1,2}}\s+(?:{MONTH_WORDS_PATTERN})\b", t)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Утилиты: текст / JSON
# ─────────────────────────────────────────────────────────────────────────────


def _clean_llm_json(response: str) -> str:
    text = response.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _fallback_meeting_title(text: str) -> str:
    patterns = [
        r"(встреч[аеуи] с [^,.!\n?]+)",
        r"(созвон[аеуи]? с [^,.!\n?]+)",
        r"(собрани[еяю] с [^,.!\n?]+)",
        r"(совещани[еяю] с [^,.!\n?]+)",
        r"(встреч[аеуи] [^,.!\n?]+)",
        r"(созвон [^,.!\n?]+)",
        r"(собрани[еяю] [^,.!\n?]+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".,!")
    return ""


def _strip_batch_headers(text: str) -> str:
    """
    Убирает из строк префиксы вида:
      [10:15] @alex: ...
      [10:15] Alex Doe: ...
    чтобы regex'ы не путали автора строки с mention'ом
    и время сообщения со временем встречи.
    """
    if not text:
        return ""
    cleaned_lines = []
    for line in text.splitlines():
        cleaned = re.sub(
            r"^\[\d{2}:\d{2}\]\s+[^:\n]{1,100}:\s*", "", line.strip()
        )
        cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)


# ─────────────────────────────────────────────────────────────────────────────
# Построение контекста дат (общий, для встреч и задач)
# ─────────────────────────────────────────────────────────────────────────────


def _build_date_context(now: datetime) -> str:
    today = now.date()
    wd = now.weekday()

    table: List[str] = [
        "=== ТАБЛИЦА ЗАМЕНЫ ОТНОСИТЕЛЬНЫХ ДАТ ===",
        "ВАЖНО: используй ТОЛЬКО значения из этой таблицы, не считай даты самостоятельно.",
        "",
        f"  сегодня               → {today.strftime('%d.%m.%Y')} ({DAY_FORMS[wd]['label_this']})",
        f"  завтра                → {(today + timedelta(days=1)).strftime('%d.%m.%Y')}",
        f"  послезавтра           → {(today + timedelta(days=2)).strftime('%d.%m.%Y')}",
        "",
    ]

    for item in DAY_FORMS:
        idx = item["weekday"]
        this_d = _next_weekday_date(today, idx)
        next_d = this_d + timedelta(days=7)
        fmt_this = this_d.strftime("%d.%m.%Y")
        fmt_next = next_d.strftime("%d.%m.%Y")

        for alias in item["aliases_this"]:
            table.append(f"  {alias:<35} → {fmt_this}")
        table.append(f"  {item['label_next']:<35} → {fmt_next}")
        for alias in item["aliases_next"]:
            table.append(f"  {alias:<35} → {fmt_next}")
        table.append("")

    _, last_day = cal_mod.monthrange(today.year, today.month)
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    table += [
        "  Правило 'N числа' (без месяца):",
        f"    если N >= {today.day} и N <= {last_day} → месяц {today.strftime('%m.%Y')}",
        f"    иначе → месяц {next_month.strftime('%m.%Y')}",
        "",
    ]

    cal_lines = [
        "=== КАЛЕНДАРЬ НА 90 ДНЕЙ ===",
    ]
    for i in range(90):
        d = today + timedelta(days=i)
        marker = " ← СЕГОДНЯ" if i == 0 else (" ← ЗАВТРА" if i == 1 else "")
        month_name = MONTH_NAMES_RU[d.month]
        label = DAY_FORMS[d.weekday()]["label_this"]
        cal_lines.append(
            f"  {label:<25} {d.day} {month_name}  {d.strftime('%d.%m.%Y')}{marker}"
        )

    return "\n".join(table + cal_lines)


# ─────────────────────────────────────────────────────────────────────────────
# Построение alias-map для детерминированного парсинга дедлайнов задач
# ─────────────────────────────────────────────────────────────────────────────


def _build_alias_map(now: datetime) -> Dict[str, str]:
    today = now.date()
    alias_map: Dict[str, str] = {
        "сегодня": today.strftime("%Y-%m-%d"),
        "завтра": (today + timedelta(days=1)).strftime("%Y-%m-%d"),
        "послезавтра": (today + timedelta(days=2)).strftime("%Y-%m-%d"),
    }
    for item in DAY_FORMS:
        this_d = _next_weekday_date(today, item["weekday"])
        next_d = this_d + timedelta(days=7)
        for alias in item["aliases_this"]:
            alias_map[alias] = this_d.strftime("%Y-%m-%d")
        for alias in item["aliases_next"]:
            alias_map[alias] = next_d.strftime("%Y-%m-%d")
    return alias_map


def _detect_due_date_fallback(
    text: str, alias_map: Dict[str, str]
) -> Optional[str]:
    text_l = f" {text.lower()} "
    for phrase in sorted(alias_map.keys(), key=len, reverse=True):
        if f" {phrase} " in text_l or text_l.endswith(f" {phrase} "):
            return alias_map[phrase]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Few-shot примеры (генерируются с реальными датами)
# ─────────────────────────────────────────────────────────────────────────────


def _build_task_examples(now: datetime) -> str:
    today = now.date()
    tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    saturday = _next_weekday_date(today, 5).strftime("%Y-%m-%d")
    sunday = _next_weekday_date(today, 6).strftime("%Y-%m-%d")

    return f"""
=== ПРИМЕРЫ ===

Сообщение: "в эту субботу @JDexMoment и @Neirix1_bot нужно подготовить отчет"
Ответ:
{{"tasks": [{{"title": "подготовить отчет", "assignees": ["@JDexMoment", "@Neirix1_bot"], "due_date": "{saturday}", "description": ""}}]}}

Сообщение: "в это воскресенье нужно провести презентацию"
Ответ:
{{"tasks": [{{"title": "провести презентацию", "assignees": [], "due_date": "{sunday}", "description": ""}}]}}

Сообщение: "к воскресенью @Neirix1_bot и @JDexMoment должны будут сделать тест по русскому языку"
Ответ:
{{"tasks": [{{"title": "сделать тест по русскому языку", "assignees": ["@Neirix1_bot", "@JDexMoment"], "due_date": "{sunday}", "description": ""}}]}}

Сообщение: "нужно послезавтра провести презентацию"
Ответ:
{{"tasks": [{{"title": "провести презентацию", "assignees": [], "due_date": "{day_after}", "description": ""}}]}}

Сообщение: "завтра нужно чтобы @JDexMoment предоставил отчет по практике"
Ответ:
{{"tasks": [{{"title": "предоставить отчет по практике", "assignees": ["@JDexMoment"], "due_date": "{tomorrow}", "description": ""}}]}}

Сообщение: "завтра у нас встреча с заказчиком в 9"
Ответ:
{{"tasks": []}}

Пачка сообщений:
[10:15] @JDexMoment: нужно сделать отчёт
[10:16] @JDexMoment: @Neirix1_bot, возьми на себя, дедлайн в эту субботу
Ответ:
{{"tasks": [{{"title": "сделать отчёт", "assignees": ["@Neirix1_bot"], "due_date": "{saturday}", "description": ""}}]}}

=== КОНЕЦ ПРИМЕРОВ ==="""


def _build_meeting_examples(now: datetime) -> str:
    today = now.date()
    tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    sunday = _next_weekday_date(today, 6).strftime("%Y-%m-%d")

    return f"""
=== ПРИМЕРЫ ===

Сообщение: "у @JDexMoment и @Neirix1_bot 5 мая в 11:30 будет встреча с генералом Гавсом"
Ответ:
{{"meetings": [{{"title": "встреча с генералом Гавсом", "participants": ["@JDexMoment", "@Neirix1_bot"], "date": "2026-05-05", "time": "11:30", "description": ""}}]}}

Сообщение: "у @JDexMoment в это воскресенье в 7 утра будет встреча с другом"
Ответ:
{{"meetings": [{{"title": "встреча с другом", "participants": ["@JDexMoment"], "date": "{sunday}", "time": "07:00", "description": ""}}]}}

Сообщение: "завтра у нас встреча с заказчиком в 9"
Ответ:
{{"meetings": [{{"title": "встреча с заказчиком", "participants": [], "date": "{tomorrow}", "time": "09:00", "description": ""}}]}}

Сообщение: "послезавтра у нас встреча с директором в 10"
Ответ:
{{"meetings": [{{"title": "встреча с директором", "participants": [], "date": "{day_after}", "time": "10:00", "description": ""}}]}}

Сообщение: "нужно купить молоко"
Ответ:
{{"meetings": []}}

Пачка сообщений:
[14:00] @JDexMoment: давайте завтра созвон
[14:01] @Neirix1_bot: ок, в 11:30 норм?
[14:02] @JDexMoment: да, давайте
Ответ:
{{"meetings": [{{"title": "созвон", "participants": ["@JDexMoment", "@Neirix1_bot"], "date": "{tomorrow}", "time": "11:30", "description": ""}}]}}

=== КОНЕЦ ПРИМЕРОВ ==="""


# ─────────────────────────────────────────────────────────────────────────────
# Формирование промптов из JSON-правил
# ─────────────────────────────────────────────────────────────────────────────


def _format_rules(rules_list: List[str]) -> str:
    lines = ["ПРАВИЛА:"]
    for i, rule in enumerate(rules_list, 1):
        lines.append(f"{i}. {rule}")
    return "\n".join(lines)


def _format_response_schema(schema: Any) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2)


def _format_summary_sections(sections: List[Dict]) -> str:
    lines = []
    for s in sections:
        lines.append(f"{s['emoji']} *{s['title']}*")
        lines.append(f"— {s['description']}")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Основной клиент
# ─────────────────────────────────────────────────────────────────────────────


class LLMClient:

    def __init__(self):
        self._chat_client = None
        self._embed_model = None

    @property
    def chat_client(self):
        if self._chat_client is None:
            from gigachat import GigaChat
            self._chat_client = GigaChat(
                credentials=settings.LLM_API_KEY,
                scope="GIGACHAT_API_PERS",
                model=settings.LLM_MODEL_NAME,
                verify_ssl_certs=False,
            )
        return self._chat_client

    @property
    def embed_model(self):
        if self._embed_model is None:
            from sentence_transformers import SentenceTransformer
            self._embed_model = SentenceTransformer(settings.EMBEDDING_MODEL)
        return self._embed_model

    async def _run_sync(self, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    # ──────────────────────────────────────────────────────────────────────
    # chat_completion
    # ──────────────────────────────────────────────────────────────────────

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        from gigachat.models import Chat, Messages, MessagesRole

        role_map = {
            "system": MessagesRole.SYSTEM,
            "assistant": MessagesRole.ASSISTANT,
            "user": MessagesRole.USER,
        }

        giga_messages = [
            Messages(
                role=role_map.get(m.get("role", "user"), MessagesRole.USER),
                content=m.get("content", ""),
            )
            for m in messages
        ]

        chat_kwargs: Dict[str, Any] = {
            "messages": giga_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if model:
            chat_kwargs["model"] = model

        response = await self._run_sync(
            self.chat_client.chat, Chat(**chat_kwargs)
        )
        return response.choices[0].message.content

    # ──────────────────────────────────────────────────────────────────────
    # generate_embedding
    # ──────────────────────────────────────────────────────────────────────

    async def generate_embedding(self, text: str) -> List[float]:
        embedding = await self._run_sync(self.embed_model.encode, text)
        return embedding.tolist()

    # ──────────────────────────────────────────────────────────────────────
    # extract_tasks_from_messages  (batch-метод, основной)
    # ──────────────────────────────────────────────────────────────────────

    async def extract_tasks_from_messages(
        self,
        message_text: str,
        current_context: str,
    ) -> List[Dict[str, Any]]:
        """
        Извлекает задачи из текста.
        Текст может быть одним сообщением или пачкой строк
        вида '[HH:MM] @author: текст'.
        """

        now = datetime.now()
        date_context = _build_date_context(now)
        alias_map = _build_alias_map(now)
        examples = _build_task_examples(now)

        # Чистим batch-заголовки для regex-анализа,
        # но в промпт отправляем оригинал (LLM видит авторов).
        content_text = _strip_batch_headers(message_text)

        deterministic_due = _detect_due_date_fallback(content_text, alias_map)
        explicit_date = _contains_explicit_date(content_text)
        regex_mentions = extract_mentions(content_text)

        logger.debug(
            "Task extraction | mentions=%s | det_due=%s | text=%r",
            regex_mentions,
            deterministic_due,
            message_text[:200],
        )

        ctx = ""
        if current_context and current_context.strip():
            ctx = (
                f"\n=== КОНТЕКСТ ===\n"
                f"{current_context.strip()}\n"
                f"=== КОНЕЦ КОНТЕКСТА ===\n"
            )

        rules_text = _format_rules(TASK_RULES["rules"])
        schema_text = _format_response_schema(TASK_RULES["response_format"])

        prompt = (
            f"Ты извлекаешь ЗАДАЧИ из рабочего фрагмента чата.\n\n"
            f"Во входном тексте может быть одно сообщение или несколько "
            f"последовательных сообщений.\n"
            f"Если это пачка, строки могут быть в формате "
            f"'[HH:MM] author: текст'.\n"
            f"Анализируй весь фрагмент целиком. Если задача описана "
            f"в нескольких соседних сообщениях, собери её в один объект "
            f"и не дублируй.\n\n"
            f"{date_context}\n\n"
            f"{examples}\n\n"
            f"{ctx}\n"
            f"{rules_text}\n\n"
            f"Ответ строго в JSON (без markdown, без пояснений):\n"
            f"{schema_text}\n\n"
            f"Фрагмент чата для анализа:\n{message_text}"
        )

        messages = [
            {"role": "system", "content": TASK_RULES["system_prompt"]},
            {"role": "user", "content": prompt},
        ]

        raw_response = ""
        try:
            raw_response = await self.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=2000,
            )
            logger.info("RAW TASK RESPONSE: %s", raw_response)

            data = json.loads(_clean_llm_json(raw_response))
            raw_tasks = data.get("tasks", [])
            if not isinstance(raw_tasks, list):
                raw_tasks = []

            normalized: List[Dict[str, Any]] = []
            seen: set = set()
            single_result = len(raw_tasks) == 1

            for task in raw_tasks:
                if not isinstance(task, dict):
                    continue

                title = (task.get("title") or "").strip()
                if not title:
                    continue

                llm_assignees = _normalize_usernames(task.get("assignees"))

                # При батче мержить regex mentions безопасно только
                # если LLM вернула ровно одну задачу — иначе авторы
                # соседних реплик попадут во все задачи.
                merged = (
                    _merge_usernames(llm_assignees, regex_mentions)
                    if single_result
                    else llm_assignees
                )

                due = _normalize_due_date(task.get("due_date"))

                # Детерминированный override тоже безопасен только
                # при одной задаче — иначе две задачи с разными
                # дедлайнами получат одинаковый.
                if single_result and deterministic_due and not explicit_date:
                    if due != deterministic_due:
                        logger.info(
                            "Override due: llm=%s -> det=%s",
                            due,
                            deterministic_due,
                        )
                    due = deterministic_due

                norm = {
                    "title": title,
                    "assignees": merged,
                    "due_date": due,
                    "description": (task.get("description") or "").strip(),
                }

                dedupe_key = (
                    norm["title"].casefold(),
                    tuple(sorted(norm["assignees"])),
                    norm["due_date"],
                    norm["description"].casefold(),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                normalized.append(norm)

            logger.info("NORMALIZED TASKS: %s", normalized)
            return normalized

        except json.JSONDecodeError as e:
            logger.error("Task JSON error: %s | raw: %s", e, raw_response)
            return []
        except Exception as e:
            logger.error("Task extraction failed: %s", e, exc_info=True)
            return []

    # ──────────────────────────────────────────────────────────────────────
    # extract_tasks_from_message  (обратная совместимость, single message)
    # ──────────────────────────────────────────────────────────────────────

    async def extract_tasks_from_message(
        self,
        message_text: str,
        current_context: str,
    ) -> List[Dict[str, Any]]:
        """Обёртка для одного сообщения — делегирует в batch-метод."""
        return await self.extract_tasks_from_messages(
            message_text=message_text,
            current_context=current_context,
        )

    # ──────────────────────────────────────────────────────────────────────
    # extract_meetings_from_messages  (batch-метод, основной)
    # ──────────────────────────────────────────────────────────────────────

    async def extract_meetings_from_messages(
        self,
        message_text: str,
        current_context: str,
    ) -> List[Dict[str, Any]]:
        """
        Извлекает встречи из текста.
        Текст может быть одним сообщением или пачкой строк
        вида '[HH:MM] @author: текст'.

        Возвращает список dict'ов с ключами:
            title, participants, start_at, description
        где start_at — ISO-строка (YYYY-MM-DDTHH:MM:SS).
        """

        now = datetime.now()
        date_context = _build_date_context(now)
        alias_map = _build_alias_map(now)
        examples = _build_meeting_examples(now)

        content_text = _strip_batch_headers(message_text)

        deterministic_date = _detect_due_date_fallback(content_text, alias_map)
        explicit_date = _contains_explicit_date(content_text)
        regex_mentions = extract_mentions(content_text)

        logger.debug(
            "Meeting extraction | mentions=%s | det_date=%s | text=%r",
            regex_mentions,
            deterministic_date,
            message_text[:200],
        )

        ctx = ""
        if current_context and current_context.strip():
            ctx = (
                f"\n=== КОНТЕКСТ ===\n"
                f"{current_context.strip()}\n"
                f"=== КОНЕЦ КОНТЕКСТА ===\n"
            )

        rules_text = _format_rules(MEETING_RULES["rules"])
        schema_text = _format_response_schema(MEETING_RULES["response_format"])

        prompt = (
            f"Ты извлекаешь ВСТРЕЧИ из рабочего фрагмента чата.\n\n"
            f"Во входном тексте может быть одно сообщение или несколько "
            f"последовательных сообщений.\n"
            f"Если это пачка, строки могут быть в формате "
            f"'[HH:MM] author: текст'.\n"
            f"Анализируй весь фрагмент целиком. Если встреча описана "
            f"в нескольких соседних сообщениях, собери её в один объект "
            f"и не дублируй.\n\n"
            f"{date_context}\n\n"
            f"{examples}\n\n"
            f"{ctx}\n"
            f"{rules_text}\n\n"
            f"Ответ строго в JSON (без markdown, без пояснений):\n"
            f"{schema_text}\n\n"
            f"Фрагмент чата для анализа:\n{message_text}"
        )

        messages = [
            {"role": "system", "content": MEETING_RULES["system_prompt"]},
            {"role": "user", "content": prompt},
        ]

        raw_response = ""
        try:
            raw_response = await self.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=2000,
            )
            logger.info("RAW MEETING RESPONSE: %s", raw_response)

            data = json.loads(_clean_llm_json(raw_response))

            # ── Поддержка обоих форматов ответа LLM ──────────
            # Новый формат: {"meetings": [...]}
            # Старый формат: {"meeting": {...} | null}
            raw_meetings: List[Dict] = []
            if "meetings" in data:
                raw_meetings = data["meetings"]
                if not isinstance(raw_meetings, list):
                    raw_meetings = [raw_meetings] if raw_meetings else []
            elif "meeting" in data:
                m = data["meeting"]
                raw_meetings = [m] if m and isinstance(m, dict) else []

            normalized: List[Dict[str, Any]] = []
            seen: set = set()
            single_result = len(raw_meetings) == 1

            for meeting in raw_meetings:
                if not isinstance(meeting, dict):
                    continue

                title = (meeting.get("title") or "").strip()
                if not title:
                    title = _fallback_meeting_title(content_text)
                if not title:
                    continue

                llm_participants = _normalize_usernames(
                    meeting.get("participants")
                )

                merged = (
                    _merge_usernames(llm_participants, regex_mentions)
                    if single_result
                    else llm_participants
                )

                # ── Собираем start_at ────────────────────────
                # LLM может вернуть:
                #   1) start_at напрямую (старый формат)
                #   2) date + time (новый формат)
                raw_start_at = meeting.get("start_at")
                raw_date = meeting.get("date")
                raw_time = meeting.get("time")

                if raw_start_at:
                    start_at = _normalize_start_at(raw_start_at)
                else:
                    date_value = _normalize_due_date(raw_date)

                    if (
                        single_result
                        and deterministic_date
                        and not explicit_date
                    ):
                        if date_value != deterministic_date:
                            logger.info(
                                "Override meeting date: llm=%s -> det=%s",
                                date_value,
                                deterministic_date,
                            )
                        date_value = deterministic_date

                    time_value = _normalize_time(raw_time)
                    start_at = _build_start_at_from_date_time(
                        date_value, time_value
                    )

                if not start_at:
                    logger.warning(
                        "Meeting skipped: no valid start_at | data=%s",
                        meeting,
                    )
                    continue

                norm = {
                    "title": title,
                    "participants": merged,
                    "start_at": start_at,
                    "description": (
                        meeting.get("description") or ""
                    ).strip(),
                }

                dedupe_key = (
                    norm["title"].casefold(),
                    tuple(sorted(norm["participants"])),
                    norm["start_at"],
                    norm["description"].casefold(),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                normalized.append(norm)

            logger.info("NORMALIZED MEETINGS: %s", normalized)
            return normalized

        except json.JSONDecodeError as e:
            logger.error("Meeting JSON error: %s | raw: %s", e, raw_response)
            return []
        except Exception as e:
            logger.error("Meeting extraction failed: %s", e, exc_info=True)
            return []

    # ──────────────────────────────────────────────────────────────────────
    # extract_meeting_from_message  (обратная совместимость, single message)
    # ──────────────────────────────────────────────────────────────────────

    async def extract_meeting_from_message(
        self,
        message_text: str,
        current_context: str,
    ) -> Optional[Dict[str, Any]]:
        """Обёртка для одного сообщения — возвращает первую встречу или None."""
        meetings = await self.extract_meetings_from_messages(
            message_text=message_text,
            current_context=current_context,
        )
        return meetings[0] if meetings else None

    # ──────────────────────────────────────────────────────────────────────
    # generate_summary
    # ──────────────────────────────────────────────────────────────────────

    async def generate_summary(
        self,
        messages_context: str,
        tasks_context: str = "",
        meetings_context: str = "",
    ) -> str:

        if not messages_context or not messages_context.strip():
            return "📭 Нет сообщений для анализа за указанный период."

        MAX_CHARS = 12_000
        was_truncated = False
        if len(messages_context) > MAX_CHARS:
            messages_context = messages_context[-MAX_CHARS:]
            was_truncated = True

        tasks_block = (
            tasks_context.strip()
            if tasks_context and tasks_context.strip()
            else "Задачи не зафиксированы."
        )
        meetings_block = (
            meetings_context.strip()
            if meetings_context and meetings_context.strip()
            else "Встречи не запланированы."
        )

        trunc_note = (
            "\n⚠️ Переписка обрезана до последних сообщений "
            "из-за большого объёма.\n"
            if was_truncated
            else ""
        )

        sections_text = _format_summary_sections(SUMMARY_RULES["sections"])
        requirements = "\n".join(
            f"- {r}" for r in SUMMARY_RULES["requirements"]
        )

        prompt = (
            f"Проанализируй переписку и создай структурированное саммари.\n"
            f"{trunc_note}\n"
            f"---\nПЕРЕПИСКА:\n{messages_context}\n"
            f"---\nЗАФИКСИРОВАННЫЕ ЗАДАЧИ:\n{tasks_block}\n"
            f"---\nЗАПЛАНИРОВАННЫЕ ВСТРЕЧИ:\n{meetings_block}\n"
            f"---\n\n"
            f"Создай саммари по следующей структуре.\n"
            f"Если по разделу нет информации — напиши 'Нет данных'.\n\n"
            f"{sections_text}\n"
            f"Требования:\n{requirements}"
        )

        messages = [
            {"role": "system", "content": SUMMARY_RULES["system_prompt"]},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await self.chat_completion(
                messages=messages, temperature=0.3, max_tokens=3000
            )
            logger.info("Summary generated | len=%d", len(result))
            return result
        except Exception as e:
            logger.error("Summary failed: %s", e, exc_info=True)
            return "⚠️ Не удалось создать саммари. Попробуйте позже."