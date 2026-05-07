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
        cal_lines.append(f"  {label:<25} {d.day} {month_name}  {d.strftime('%d.%m.%Y')}{marker}")

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


def _detect_due_date_fallback(text: str, alias_map: Dict[str, str]) -> Optional[str]:
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
{{"meeting": {{"title": "встреча с генералом Гавсом", "start_at": "2026-05-05T11:30:00", "participants": ["@JDexMoment", "@Neirix1_bot"]}}}}

Сообщение: "у @JDexMoment в это воскресенье в 7 утра будет встреча с другом"
Ответ:
{{"meeting": {{"title": "встреча с другом", "start_at": "{sunday}T07:00:00", "participants": ["@JDexMoment"]}}}}

Сообщение: "завтра у нас встреча с заказчиком в 9"
Ответ:
{{"meeting": {{"title": "встреча с заказчиком", "start_at": "{tomorrow}T09:00:00", "participants": []}}}}

Сообщение: "послезавтра у нас встреча с директором в 10"
Ответ:
{{"meeting": {{"title": "встреча с директором", "start_at": "{day_after}T10:00:00", "participants": []}}}}

Сообщение: "нужно купить молоко"
Ответ:
{{"meeting": null}}

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

        response = await self._run_sync(self.chat_client.chat, Chat(**chat_kwargs))
        return response.choices[0].message.content

    # ──────────────────────────────────────────────────────────────────────
    # generate_embedding
    # ──────────────────────────────────────────────────────────────────────

    async def generate_embedding(self, text: str) -> List[float]:
        embedding = await self._run_sync(self.embed_model.encode, text)
        return embedding.tolist()

    # ──────────────────────────────────────────────────────────────────────
    # extract_tasks_from_message
    # ──────────────────────────────────────────────────────────────────────

    async def extract_tasks_from_message(
        self,
        message_text: str,
        current_context: str,
    ) -> List[Dict[str, Any]]:

        now = datetime.now()
        date_context = _build_date_context(now)
        alias_map = _build_alias_map(now)
        examples = _build_task_examples(now)

        deterministic_due = _detect_due_date_fallback(message_text, alias_map)
        explicit_date = _contains_explicit_date(message_text)
        regex_mentions = extract_mentions(message_text)

        logger.debug(
            "Task extraction | mentions=%s | det_due=%s | text=%r",
            regex_mentions, deterministic_due, message_text,
        )

        ctx = ""
        if current_context and current_context.strip():
            ctx = f"\n=== КОНТЕКСТ ===\n{current_context.strip()}\n=== КОНЕЦ КОНТЕКСТА ===\n"

        rules_text = _format_rules(TASK_RULES["rules"])
        schema_text = _format_response_schema(TASK_RULES["response_format"])

        prompt = (
            f"Ты извлекаешь ЗАДАЧИ из рабочего сообщения.\n\n"
            f"{date_context}\n\n"
            f"{examples}\n\n"
            f"{ctx}\n"
            f"{rules_text}\n\n"
            f"Ответ строго в JSON (без markdown, без пояснений):\n"
            f"{schema_text}\n\n"
            f"Сообщение для анализа:\n{message_text}"
        )

        messages = [
            {"role": "system", "content": TASK_RULES["system_prompt"]},
            {"role": "user", "content": prompt},
        ]

        raw_response = ""
        try:
            raw_response = await self.chat_completion(messages=messages, temperature=0.1, max_tokens=2000)
            logger.info("RAW TASK RESPONSE: %s", raw_response)

            data = json.loads(_clean_llm_json(raw_response))
            raw_tasks = data.get("tasks", [])
            if not isinstance(raw_tasks, list):
                raw_tasks = []

            normalized: List[Dict[str, Any]] = []
            for task in raw_tasks:
                if not isinstance(task, dict):
                    continue
                title = (task.get("title") or "").strip()
                if not title:
                    continue

                llm_assignees = _normalize_usernames(task.get("assignees"))
                merged = _merge_usernames(llm_assignees, regex_mentions)

                due = _normalize_due_date(task.get("due_date"))

                if deterministic_due and not explicit_date:
                    if due != deterministic_due:
                        logger.info("Override due: llm=%s -> det=%s", due, deterministic_due)
                    due = deterministic_due

                normalized.append({
                    "title": title,
                    "assignees": merged,
                    "due_date": due,
                    "description": (task.get("description") or "").strip(),
                })

            logger.info("NORMALIZED TASKS: %s", normalized)
            return normalized

        except json.JSONDecodeError as e:
            logger.error("Task JSON error: %s | raw: %s", e, raw_response)
            return []
        except Exception as e:
            logger.error("Task extraction failed: %s", e, exc_info=True)
            return []

    # ──────────────────────────────────────────────────────────────────────
    # extract_meeting_from_message
    # ──────────────────────────────────────────────────────────────────────

    async def extract_meeting_from_message(
        self,
        message_text: str,
        current_context: str,
    ) -> Optional[Dict[str, Any]]:

        now = datetime.now()
        date_context = _build_date_context(now)
        examples = _build_meeting_examples(now)
        regex_mentions = extract_mentions(message_text)

        logger.debug("Meeting extraction | mentions=%s | text=%r", regex_mentions, message_text)

        ctx = ""
        if current_context and current_context.strip():
            ctx = f"\n=== КОНТЕКСТ ===\n{current_context.strip()}\n=== КОНЕЦ КОНТЕКСТА ===\n"

        rules_text = _format_rules(MEETING_RULES["rules"])
        schema_meeting = _format_response_schema(MEETING_RULES["response_format_meeting"])
        schema_null = _format_response_schema(MEETING_RULES["response_format_null"])

        prompt = (
            f"Ты извлекаешь ВСТРЕЧИ из рабочего сообщения.\n\n"
            f"{date_context}\n\n"
            f"{examples}\n\n"
            f"{ctx}\n"
            f"{rules_text}\n\n"
            f"Ответ строго в JSON (без markdown, без пояснений):\n"
            f"{schema_meeting}\n"
            f"или {schema_null} если встречи нет.\n\n"
            f"Сообщение для анализа:\n{message_text}"
        )

        messages = [
            {"role": "system", "content": MEETING_RULES["system_prompt"]},
            {"role": "user", "content": prompt},
        ]

        raw_response = ""
        try:
            raw_response = await self.chat_completion(messages=messages, temperature=0.1, max_tokens=1000)
            logger.info("RAW MEETING RESPONSE: %s", raw_response)

            data = json.loads(_clean_llm_json(raw_response))
            meeting = data.get("meeting")
            if not meeting or not isinstance(meeting, dict):
                return None

            title = (meeting.get("title") or "").strip()
            if not title:
                title = _fallback_meeting_title(message_text)

            start_at = _normalize_start_at(meeting.get("start_at"))
            if not start_at:
                logger.warning("Bad start_at: %s", meeting.get("start_at"))
                return None

            llm_participants = _normalize_usernames(meeting.get("participants"))
            merged = _merge_usernames(llm_participants, regex_mentions)

            result = {"title": title, "start_at": start_at, "participants": merged}
            logger.info("NORMALIZED MEETING: %s", result)
            return result

        except json.JSONDecodeError as e:
            logger.error("Meeting JSON error: %s | raw: %s", e, raw_response)
            return None
        except Exception as e:
            logger.error("Meeting extraction failed: %s", e, exc_info=True)
            return None

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

        tasks_block = tasks_context.strip() if tasks_context and tasks_context.strip() else "Задачи не зафиксированы."
        meetings_block = meetings_context.strip() if meetings_context and meetings_context.strip() else "Встречи не запланированы."

        trunc_note = (
            "\n⚠️ Переписка обрезана до последних сообщений из-за большого объёма.\n"
            if was_truncated else ""
        )

        sections_text = _format_summary_sections(SUMMARY_RULES["sections"])
        requirements = "\n".join(f"- {r}" for r in SUMMARY_RULES["requirements"])

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
            result = await self.chat_completion(messages=messages, temperature=0.3, max_tokens=3000)
            logger.info("Summary generated | len=%d", len(result))
            return result
        except Exception as e:
            logger.error("Summary failed: %s", e, exc_info=True)
            return "⚠️ Не удалось создать саммари. Попробуйте позже."