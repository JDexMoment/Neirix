import asyncio
import calendar as cal_mod
import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

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

COMBINED_RULES: Dict = _load_json("combined_rules.json")
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

def _build_date_context_compact(now: datetime, days: int = 7) -> str:
    """Компактный календарь на 7 дней."""
    today = now.date()
    lines_out: List[str] = [
        "=== ТАБЛИЦА ЗАМЕНЫ ДАТ (только эти значения) ===",
        f"  сегодня      → {today.strftime('%d.%m.%Y')}",
        f"  завтра       → {(today + timedelta(days=1)).strftime('%d.%m.%Y')}",
        f"  послезавтра  → {(today + timedelta(days=2)).strftime('%d.%m.%Y')}",
        "",
    ]
    for item in DAY_FORMS:
        idx = item["weekday"]
        this_d = _next_weekday_date(today, idx)
        next_d = this_d + timedelta(days=7)
        lines_out.append(f"  {item['label_this']:<30} → {this_d.strftime('%d.%m.%Y')}")
        lines_out.append(f"  {item['label_next']:<30} → {next_d.strftime('%d.%m.%Y')}")
    _, last_day = cal_mod.monthrange(today.year, today.month)
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    lines_out += [
        "",
        f"  Правило 'N числа': если N ≥ {today.day} и N ≤ {last_day} → "
        f"{today.strftime('%m.%Y')}, иначе → {next_month.strftime('%m.%Y')}",
        "",
        f"=== КАЛЕНДАРЬ НА {days} ДНЕЙ ===",
    ]
    for i in range(days):
        d = today + timedelta(days=i)
        marker = " ← СЕГОДНЯ" if i == 0 else (" ← ЗАВТРА" if i == 1 else "")
        label = DAY_FORMS[d.weekday()]["label_this"]
        lines_out.append(
            f"  {d.day} {MONTH_NAMES_RU[d.month]} ({label})  "
            f"{d.strftime('%d.%m.%Y')}{marker}"
        )
    return "\n".join(lines_out)

def _build_combined_examples(now: datetime) -> str:
    """4 разнообразных few-shot примера."""
    today = now.date()
    tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")
    friday = _next_weekday_date(today, 4).strftime("%Y-%m-%d")
    sunday = _next_weekday_date(today, 6).strftime("%Y-%m-%d")
    return (
        "=== ПРИМЕРЫ ===\n\n"
        f'Сообщение: "в эту субботу @JDexMoment нужно подготовить отчет"\n'
        f'Ответ: {{"tasks":[{{"title":"подготовить отчет",'
        f'"assignees":["@JDexMoment"],"due_date":"{sunday}","description":""}}],"meetings":[]}}\n\n'
        f'Сообщение: "завтра в 14:30 у @JDexMoment и @D1MRUS созвон с заказчиком"\n'
        f'Ответ: {{"tasks":[],"meetings":[{{"title":"созвон с заказчиком",'
        f'"participants":["@JDexMoment","@D1MRUS"],"date":"{tomorrow}","time":"14:30","description":""}}]}}\n\n'
        f'Сообщение: "завтра @D1MRUS должен сделать отчёт, а в 10 у нас общее собрание, всем быть"\n'
        f'Ответ: {{"tasks":[{{"title":"сделать отчёт","assignees":["@D1MRUS"],'
        f'"due_date":"{tomorrow}","description":""}}],'
        f'"meetings":[{{"title":"общее собрание","participants":["Все участники"],'
        f'"date":"{tomorrow}","time":"10:00","description":""}}]}}\n\n'
        f'Пачка сообщений:\n'
        f'[09:15] @JDexMoment: @Neirix1_bot, скинь отчёт\n'
        f'[09:16] @Neirix1_bot: ок, сегодня до 18:00 сделаю\n'
        f'Ответ: {{"tasks":[{{"title":"скинуть отчёт",'
        f'"assignees":["@Neirix1_bot"],"due_date":"{today_str}","description":""}}],"meetings":[]}}\n\n'
        "=== КОНЕЦ ПРИМЕРОВ ===\n"
    )
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
        temperature: float = 0.3,
        max_tokens: int = 1500,
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

        try:
            response = await self._run_sync(
                self.chat_client.chat, Chat(**chat_kwargs)
            )
            return response.choices[0].message.content
        except Exception as e:
            # Check if it's a payment required error
            if "402" in str(e) or "Payment Required" in str(e):
                logger.error("GigaChat API payment required error: %s", e)
                # Return a default response or handle gracefully
                return '{"tasks": [], "meetings": []}'  # Generic default response
            else:
                raise e

    # ──────────────────────────────────────────────────────────────────────
    # generate_embedding
    # ──────────────────────────────────────────────────────────────────────

    async def generate_embedding(self, text: str) -> List[float]:
        embedding = await self._run_sync(self.embed_model.encode, text)
        return embedding.tolist()

    # ──────────────────────────────────────────────────────────────────────
    # extract_all_from_messages
    # ──────────────────────────────────────────────────────────────────────

    async def extract_all_from_messages(
        self,
        messages: list,
    ) -> Optional[Dict[str, List[Dict]]]:
        """Извлекает задачи И встречи за ОДИН LLM-вызов. Экономия ~50% токенов."""
        if not messages:
            return {"tasks": [], "meetings": []}

        messages = sorted(messages, key=lambda m: m.timestamp)

        context_lines = []
        for msg in messages:
            author = (
                f"@{msg.author.username}"
                if msg.author and msg.author.username
                else (msg.author.full_name or str(msg.author.telegram_id))
            )
            time_str = timezone.localtime(msg.timestamp).strftime("%H:%M")
            context_lines.append(f"[{time_str}] {author}: {msg.text}")

        batch_text = "\n".join(context_lines)

        now = datetime.now()
        date_context = _build_date_context_compact(now, days=14)
        alias_map = _build_alias_map(now)
        examples = _build_combined_examples(now)

        content_text = _strip_batch_headers(batch_text)
        deterministic_date = _detect_due_date_fallback(content_text, alias_map)
        explicit_date = _contains_explicit_date(content_text)
        regex_mentions = extract_mentions(content_text)

        rules_text = _format_rules(COMBINED_RULES["rules"])
        schema_text = _format_response_schema(COMBINED_RULES["response_format"])

        prompt = (
            f"Извлеки задачи и встречи из фрагмента чата.\n"
            f"Анализируй весь фрагмент целиком.\n\n"
            f"{date_context}\n\n"
            f"{examples}\n\n"
            f"{rules_text}\n\n"
            f"Ответ СТРОГО в JSON (без пояснений):\n"
            f"{schema_text}\n\n"
            f"Фрагмент чата:\n{batch_text}"
        )

        fm = [
            {"role": "system", "content": COMBINED_RULES["system_prompt"]},
            {"role": "user", "content": prompt},
        ]

        raw_response = ""
        try:
            raw_response = await self.chat_completion(
                messages=fm, temperature=0.1, max_tokens=2000,
            )
            logger.info("RAW COMBINED RESPONSE: %s", raw_response)
            data = json.loads(_clean_llm_json(raw_response))

            raw_tasks = data.get("tasks", [])
            raw_meetings = data.get("meetings", [])

            # Нормализация задач
            normalized_tasks: List[Dict] = []
            seen_tasks: set = set()
            single_task = len(raw_tasks) == 1

            for task in (raw_tasks if isinstance(raw_tasks, list) else []):
                if not isinstance(task, dict):
                    continue
                title = (task.get("title") or "").strip()
                if not title:
                    continue
                llm_assignees = _normalize_usernames(task.get("assignees"))
                merged = (
                    _merge_usernames(llm_assignees, regex_mentions)
                    if single_task else llm_assignees
                )
                due_date = _normalize_due_date(task.get("due_date"))
                if (single_task and deterministic_date and not explicit_date
                        and due_date != deterministic_date):
                    due_date = deterministic_date
                desc = (task.get("description") or "").strip()
                dedupe_key = (title.casefold(), tuple(sorted(merged)), due_date, desc.casefold())
                if dedupe_key in seen_tasks:
                    continue
                seen_tasks.add(dedupe_key)
                normalized_tasks.append({
                    "title": title, "assignees": merged,
                    "due_date": due_date, "description": desc,
                })

            # Нормализация встреч
            normalized_meetings: List[Dict] = []
            seen_meetings: set = set()
            single_meeting = len(raw_meetings) == 1

            for meeting in (raw_meetings if isinstance(raw_meetings, list) else []):
                if not isinstance(meeting, dict):
                    continue
                title = (meeting.get("title") or "").strip()
                if not title:
                    title = _fallback_meeting_title(content_text)
                if not title:
                    continue
                llm_participants = _normalize_usernames(meeting.get("participants"))
                merged = (
                    _merge_usernames(llm_participants, regex_mentions)
                    if single_meeting else llm_participants
                )
                raw_start_at = meeting.get("start_at")
                raw_date = meeting.get("date")
                raw_time = meeting.get("time")
                if raw_start_at:
                    start_at = _normalize_start_at(raw_start_at)
                else:
                    date_value = _normalize_due_date(raw_date)
                    if (single_meeting and deterministic_date and not explicit_date
                            and date_value != deterministic_date):
                        date_value = deterministic_date
                    time_value = _normalize_time(raw_time)
                    start_at = _build_start_at_from_date_time(date_value, time_value)
                if not start_at:
                    continue
                desc = (meeting.get("description") or "").strip()
                dedupe_key = (title.casefold(), tuple(sorted(merged)), start_at, desc.casefold())
                if dedupe_key in seen_meetings:
                    continue
                seen_meetings.add(dedupe_key)
                normalized_meetings.append({
                    "title": title, "participants": merged,
                    "start_at": start_at, "description": desc,
                })

            logger.info("COMBINED: tasks=%d meetings=%d",
                        len(normalized_tasks), len(normalized_meetings))
            return {"tasks": normalized_tasks, "meetings": normalized_meetings}

        except json.JSONDecodeError as e:
            logger.error("Combined JSON error: %s | raw: %s", e, raw_response)
            return None
        except Exception as e:
            logger.error("Combined extraction failed: %s", e, exc_info=True)
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
                messages=messages, temperature=0.2, max_tokens=2000
            )
            logger.info("Summary generated | len=%d", len(result))
            return result
        except Exception as e:
            logger.error("Summary failed: %s", e, exc_info=True)
            return "⚠️ Не удалось создать саммари. Попробуйте позже."