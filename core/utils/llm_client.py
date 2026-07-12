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
COMBINED_RULES: Dict = _load_json("combined_rules.json")
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
# Формирование промптов из JSON-правил
# ─────────────────────────────────────────────────────────────────────────────


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
    @staticmethod
    def _next_weekday_date(base_date, target_weekday: int):
        diff = (target_weekday - base_date.weekday()) % 7
        return base_date + timedelta(days=diff if diff > 0 else 7)

    def _build_alias_map(self, now):
        """Build a map of date aliases to actual dates"""
        today = now.date()
        alias_map = {
            "сегодня": today.strftime("%Y-%m-%d"),
            "завтра": (today + timedelta(days=1)).strftime("%Y-%m-%d"),
            "послезавтра": (today + timedelta(days=2)).strftime("%Y-%m-%d"),
        }
        for item in DAY_FORMS:
            this_d = self._next_weekday_date(today, item["weekday"])
            next_d = this_d + timedelta(days=7)
            for alias in item["aliases_this"]:
                alias_map[alias] = this_d.strftime("%Y-%m-%d")
            for alias in item["aliases_next"]:
                alias_map[alias] = next_d.strftime("%Y-%m-%d")
        return alias_map

    def _detect_due_date_fallback(self, text, alias_map):
        """Detect due date from text using alias map"""
        text_l = f" {text.lower()} "
        for phrase in sorted(alias_map.keys(), key=len, reverse=True):
            if f" {phrase} " in text_l or text_l.endswith(f" {phrase} "):
                return alias_map[phrase]
        return None

    def _strip_batch_headers(self, text):
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

    def _clean_llm_json(self, response):
        """Clean JSON response from LLM"""
        text = response.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def _normalize_usernames(self, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []

        result = []
        seen = set()
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
                if not clean:
                    continue
                # ═══ ОСТАВЛЯЕМ "Все участники", "All participants" КАК ЕСТЬ ═══
                if re.match(r"^[A-Za-z0-9_]{1,32}$", clean):
                    username = f"@{clean}"
                    low = username.lower()
                    if low not in seen:
                        seen.add(low)
                        result.append(username)
                else:
                    # Не-username значения (кириллица, пробелы) — оставляем как есть
                    low = clean.lower()
                    if low not in seen:
                        seen.add(low)
                        result.append(clean)
        return result

    def _merge_usernames(self, from_llm, from_regex):
        """Merge usernames from LLM response and regex extraction"""
        seen = set()
        result = []
        for username in from_llm + from_regex:
            low = username.lower()
            if low not in seen:
                seen.add(low)
                result.append(username)
        return result

    def _normalize_due_date(self, value):
        """Normalize due date to standard format"""
        if not value or not isinstance(value, str):
            return None
        value = value.strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    def _normalize_start_at(self, value):
        """Normalize start time to standard format"""
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

    def _normalize_time(self, value):
        """Normalize time to standard format"""
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

    def _build_start_at_from_date_time(self, date_str, time_str):
        """Build start time from separate date and time strings"""
        if not date_str:
            return None
        normalized_time = self._normalize_time(time_str) if time_str else None
        if normalized_time:
            return f"{date_str}T{normalized_time}:00"
        return f"{date_str}T09:00:00"

    def _contains_explicit_date(self, text):
        """Check if text contains explicit date"""
        t = text.lower()
        return bool(
            re.search(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b", t)
            or re.search(rf"\b\d{{1,2}}\s+(?:{MONTH_WORDS_PATTERN})\b", t)
        )

    def _fallback_meeting_title(self, text):
        """Extract meeting title from text as fallback"""
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

    def extract_mentions(self, text):
        """Extract mentions from text"""
        seen = set()
        result = []
        for username in USERNAME_RE.findall(text):
            low = username.lower()
            if low not in seen:
                seen.add(low)
                result.append(username)
        return result

    def _format_rules(self, rules_list):
        """Format rules list for prompt"""
        lines = ["ПРАВИЛА:"]
        for i, rule in enumerate(rules_list, 1):
            lines.append(f"{i}. {rule}")
        return "\n".join(lines)

    def _format_response_schema(self, schema):
        """Format response schema as JSON for prompt"""
        return json.dumps(schema, ensure_ascii=False, indent=2)

    def _build_date_context_compact(self, now):
        """Build compact date context for prompts"""
        from calendar import monthrange
        today = now.date()

        lines = [
            "=== ТАБЛИЦА ЗАМЕНЫ ДАТ (используй ТОЛЬКО эти значения) ===",
            f"  сегодня      -> {today.strftime('%d.%m.%Y')}",
            f"  завтра       -> {(today + timedelta(days=1)).strftime('%d.%m.%Y')}",
            f"  послезавтра  -> {(today + timedelta(days=2)).strftime('%d.%m.%Y')}",
            "",
        ]

        for item in DAY_FORMS:
            idx = item["weekday"]
            this_d = self._next_weekday_date(today, idx)
            next_d = this_d + timedelta(days=7)
            fmt_this = this_d.strftime("%d.%m.%Y")
            fmt_next = next_d.strftime("%d.%m.%Y")
            lines.append(f"  {item['label_this']:<30} -> {fmt_this}")
            lines.append(f"  {item['label_next']:<30} -> {fmt_next}")

        _, last_day = monthrange(today.year, today.month)
        next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        lines += [
            "",
            f"  Правило 'N числа': если N>={today.day} и N<={last_day} -> {today.strftime('%m.%Y')}, иначе -> {next_month.strftime('%m.%Y')}",
            "",
            "=== КАЛЕНДАРЬ НА 14 ДНЕЙ ===",
        ]

        for i in range(14):  # 14 вместо 90
            d = today + timedelta(days=i)
            marker = " <-- СЕГОДНЯ" if i == 0 else (" <-- ЗАВТРА" if i == 1 else "")
            month_name = MONTH_NAMES_RU[d.month]
            label = DAY_FORMS[d.weekday()]["label_this"]
            lines.append(f"  {d.day} {month_name} ({label})  {d.strftime('%d.%m.%Y')}{marker}")

        return "\n".join(lines)

    def _build_combined_examples(self, now):
        """Build combined examples for task and meeting extraction"""
        from datetime import timedelta
        today = now.date()
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")
        after_tomorrow = (today + timedelta(days=2)).strftime("%Y-%m-%d")

        # Находим воскресенье
        this_sunday = self._next_weekday_date(today, 6)
        sunday = this_sunday.strftime("%Y-%m-%d")

        return (
            "=== ПРИМЕРЫ ===\n"
            "\n"
            f'Сообщение: "послезавтра @JDexMoment нужно сдать отчет"\n'
            f'Ответ: {{"tasks":[{{"title":"сдать отчет","assignees":["@JDexMoment"],"due_date":"{after_tomorrow}","description":""}}],"meetings":[]}}\n'
            "\n"
            f'Сообщение: "15 числа собрание в 14"\n'
            f'Ответ: {{"tasks":[],"meetings":[{{"title":"собрание","participants":[],"date":"{today.year:04d}-{today.month:02d}-15","time":"14:00","description":""}}]}}\n'
            "\n"
            f'Сообщение: "в эту субботу @JDexMoment нужно подготовить отчет"\n'
            f'Ответ: {{"tasks":[{{"title":"подготовить отчет","assignees":["@JDexMoment"],"due_date":"{sunday}","description":""}}],"meetings":[]}}\n'
            "\n"
            f'Сообщение: "завтра у @JDexMoment в 11:30 встреча с генералом"\n'
            f'Ответ: {{"tasks":[],"meetings":[{{"title":"встреча с генералом","participants":["@JDexMoment"],"date":"{tomorrow}","time":"11:30","description":""}}]}}\n'
            "\n"
            f'Сообщение: "завтра @D1MRUS должен сделать отчет и в 14 у него встреча с директором"\n'
            f'Ответ: {{"tasks":[{{"title":"сделать отчет","assignees":["@D1MRUS"],"due_date":"{tomorrow}","description":""}}],"meetings":[{{"title":"встреча с директором","participants":["@D1MRUS"],"date":"{tomorrow}","time":"14:00","description":""}}]}}\n'
            "\n"
            f'Сообщение: "нужно купить молоко"\n'
            f'Ответ: {{"tasks":[],"meetings":[]}}\n'
            "\n"
            f'Сообщение: "задача для @JDexMoment - прибраться в комнате сегодня"\n'
            f'Ответ: {{"tasks":[{{"title":"прибраться в комнате","assignees":["@JDexMoment"],"due_date":"{today_str}","description":""}}],"meetings":[]}}\n'
            "\n"
            f'Сообщение: "давайте завтра созвон в 11:30 @JDexMoment @Neirix1_bot"\n'
            f'Ответ: {{"tasks":[],"meetings":[{{"title":"созвон","participants":["@JDexMoment","@Neirix1_bot"],"date":"{tomorrow}","time":"11:30","description":""}}]}}\n'
            "\n"
            "=== КОНЕЦ ПРИМЕРОВ ===\n"
        )

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
        self,  # self = LLMClient instance
        messages: list,
    ) -> Optional[Dict[str, List[Dict]]]:
        """
        Извлекает задачи и встречи за ОДИН LLM-вызов.

        Требует импорта хелперов из оригинального llm_client.py:
        _build_alias_map, _detect_due_date_fallback, _strip_batch_headers,
        _clean_llm_json, _normalize_usernames, _merge_usernames,
        _normalize_due_date, _normalize_start_at, _normalize_time,
        _build_start_at_from_date_time, _contains_explicit_date,
        _fallback_meeting_title, extract_mentions, _format_rules,
        _format_response_schema, MONTH_NAMES_RU, DAY_FORMS, _next_weekday_date,
        _load_json, _PROMPTS_DIR
        """
        if not messages:
            return {"tasks": [], "meetings": []}

        from django.utils import timezone
        from datetime import datetime, timedelta
        import json

        # Используем уже определенные в модуле переменные
        COMBINED_RULES = _load_json("combined_rules.json")

        messages = sorted(messages, key=lambda m: m.timestamp)

        # Формируем текст батча
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
        date_context = self._build_date_context_compact(now)
        alias_map = self._build_alias_map(now)
        examples = self._build_combined_examples(now)

        content_text = self._strip_batch_headers(batch_text)
        deterministic_date = self._detect_due_date_fallback(content_text, alias_map)
        explicit_date = self._contains_explicit_date(content_text)
        regex_mentions = self.extract_mentions(content_text)

        rules_text = self._format_rules(COMBINED_RULES["rules"])
        schema_text = self._format_response_schema(COMBINED_RULES["response_format"])

        prompt = (
            f"Извлеки задачи и встречи из фрагмента чата.\n"
            f"Анализируй весь фрагмент целиком.\n\n"
            f"{date_context}\n\n"
            f"{rules_text}\n\n"
            f"{examples}\n\n"
            f"Ответ СТРОГО в JSON:\n"
            f"{schema_text}\n\n"
            f"Фрагмент чата:\n{batch_text}"
        )

        full_messages = [
            {"role": "system", "content": COMBINED_RULES["system_prompt"]},
            {"role": "user", "content": prompt},
        ]

        raw_response = ""
        try:
            raw_response = await self.chat_completion(
                messages=full_messages,
                temperature=0.1,
                max_tokens=2000,
            )
            logger.info("RAW COMBINED RESPONSE: %s", raw_response)

            data = json.loads(self._clean_llm_json(raw_response))

            raw_tasks = data.get("tasks", [])
            raw_meetings = data.get("meetings", [])

            # ── Нормализация задач ──────────────────────────────
            normalized_tasks = []
            seen_tasks = set()
            single_task = len(raw_tasks) == 1

            for task in (raw_tasks if isinstance(raw_tasks, list) else []):
                if not isinstance(task, dict):
                    continue
                title = (task.get("title") or "").strip()
                if not title:
                    continue

                llm_assignees = self._normalize_usernames(task.get("assignees"))
                merged = (
                    self._merge_usernames(llm_assignees, regex_mentions)
                    if single_task
                    else llm_assignees
                )

                due_date = self._normalize_due_date(task.get("due_date"))
                if (
                    single_task
                    and deterministic_date
                    and not explicit_date
                    and due_date != deterministic_date
                ):
                    due_date = deterministic_date

                desc = (task.get("description") or "").strip()

                dedupe_key = (title.casefold(), tuple(sorted(merged)), due_date, desc.casefold())
                if dedupe_key in seen_tasks:
                    continue
                seen_tasks.add(dedupe_key)

                normalized_tasks.append({
                    "title": title,
                    "assignees": merged,
                    "due_date": due_date,
                    "description": desc,
                })

            # ── Нормализация встреч ─────────────────────────────
            normalized_meetings = []
            seen_meetings = set()
            single_meeting = len(raw_meetings) == 1

            for meeting in (raw_meetings if isinstance(raw_meetings, list) else []):
                if not isinstance(meeting, dict):
                    continue
                title = (meeting.get("title") or "").strip()
                if not title:
                    title = self._fallback_meeting_title(content_text)
                if not title:
                    continue

                llm_participants = self._normalize_usernames(meeting.get("participants"))
                merged = (
                    self._merge_usernames(llm_participants, regex_mentions)
                    if single_meeting
                    else llm_participants
                )

                raw_start_at = meeting.get("start_at")
                raw_date = meeting.get("date")
                raw_time = meeting.get("time")

                if raw_start_at:
                    start_at = self._normalize_start_at(raw_start_at)
                else:
                    date_value = self._normalize_due_date(raw_date)
                    if (
                        single_meeting
                        and deterministic_date
                        and not explicit_date
                        and date_value != deterministic_date
                    ):
                        date_value = deterministic_date
                    time_value = self._normalize_time(raw_time)
                    start_at = self._build_start_at_from_date_time(date_value, time_value)

                if not start_at:
                    continue

                desc = (meeting.get("description") or "").strip()

                dedupe_key = (
                    title.casefold(),
                    tuple(sorted(merged)),
                    start_at,
                    desc.casefold(),
                )
                if dedupe_key in seen_meetings:
                    continue
                seen_meetings.add(dedupe_key)

                normalized_meetings.append({
                    "title": title,
                    "participants": merged,
                    "start_at": start_at,
                    "description": desc,
                })

            logger.info(
                "COMBINED: tasks=%d meetings=%d",
                len(normalized_tasks), len(normalized_meetings),
            )
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
                messages=messages, temperature=0.3, max_tokens=3000
            )
            logger.info("Summary generated | len=%d", len(result))
            return result
        except Exception as e:
            logger.error("Summary failed: %s", e, exc_info=True)
            return "⚠️ Не удалось создать саммари. Попробуйте позже."