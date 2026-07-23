# Руководство разработчика Neirix

## 1. Настройка окружения

### 1.1. Требования
- Python 3.12+
- Docker и Docker Compose (для баз данных и брокера)
- Git

### 1.2. Клонирование и виртуальное окружение
git clone https://github.com/JDexMoment/Neirix.git
cd Neirix
python3 -m venv venv
source venv/bin/activate   # Linux/MacOS
venv\Scripts\activate   # Windows

### 1.3. Установка зависимостей
pip install -r requirements.txt

### 1.4 Переменные окружения
Создайте файл .env в корне проекта на основе .env.example и заполните обязательные переменные:

SECRET_KEY=...              # секретный ключ Django
TELEGRAM_BOT_TOKEN=...      # токен бота
LLM_API_KEY=...             # ключ GigaChat API
LLM_MODEL_NAME=...          # модель GigaChat (например, GigaChat-Plus)
EMBEDDING_MODEL=...         # модель для эмбеддингов (all-MiniLM-L6-v2)
POSTGRES_DB=neirix
POSTGRES_USER=...
POSTGRES_PASSWORD=...
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
QDRANT_URL=http://localhost:6333

### 1.5 Запуск инфраструктуры
docker-compose up -d   # PostgreSQL, Redis, Qdrant

### 1.6 Миграции и суперпользователь
python manage.py migrate
python manage.py createsuperuser

### 1.7 Запуск бота и Celery

Откройте 3 терминала: 

Терминал 1 – Celery Worker
celery -A config worker --loglevel=info --pool=solo

Терминал 2 – Celery Beat (планировщик)
celery -A config beat --loglevel=info

Терминал 3 – Telegram-бот
python bot/main.py

## 2. Структура проекта

Neirix/
├── bot/                     # Telegram-слой
│   ├── handlers/            # Обработчики команд и callback-запросов
│   │   ├── summary.py
│   │   ├── tasks.py
│   │   ├── meetings.py
│   │   ├── chat_events.py
│   │   ├── chat_link.py
│   │   ├── messages.py
│   │   └── roles.py
│   ├── keyboards/
│   │   └── inline.py        # все inline-клавиатуры
│   ├── services/
│   │   └── notification_sender.py
│   ├── middlewares/
│   │   └── fsm_timeout.py   # таймаут для FSM
│   ├── states.py            # состояния FSM (перенос встреч)
│   ├── db_utils.py          # синхронные обёртки для работы с БД
│   ├── utils.py             # вспомогательные утилиты (get_chat_context)
│   └── main.py              # точка входа, инициализация бота
├── core/                    # Бизнес-логика и модели Django
│   ├── models.py            # все модели (Task, Meeting, Summary, ComparisonSummary и др.)
│   ├── services/
│   │   ├── task_service.py
│   │   ├── meeting_service.py
│   │   └── summary_service.py
│   ├── utils/
│   │   ├── llm_client.py    # клиент для GigaChat и эмбеддингов
│   │   └── prompts/         # JSON-промпты для LLM
│   │       ├── task_rules.json
│   │       ├── meeting_rules.json
│   │       ├── combined_rules.json
│   │       ├── summary_rules.json
│   │       └── comparison_rules.json
│   └── ...
├── celery_app/              # Фоновые задачи Celery
│   ├── tasks/
│   │   ├── send_reminders.py
│   │   ├── process_messages.py
│   │   └── generate_summary.py
│   └── schedule.py          # расписание периодических задач
├── vector_store/            # Векторное хранилище Qdrant
│   ├── client.py
│   └── embeddings.py
├── config/                  # Настройки Django и Celery
│   ├── settings.py
│   ├── celery.py
│   ├── urls.py
│   └── wsgi.py
├── tests/                   # Тесты pytest
│   ├── bot/
│   ├── celery_app/
│   └── conftest.py
├── docs/                    # Документация
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── manage.py

## 3. Архитектура и основные потоки данных

Здесь будет диаграмма компонентов и описание ключевых процессов

## 4. Конфигурация и переменные окружения

Все настройки, меняющиеся от окружения, вынесены в .env и загружаются в config/settings.py через python-dotenv. Основные группы:
Переменная -- Назначение
SECRET_KEY -- Криптографический ключ Django
TELEGRAM_BOT_TOKEN	-- Токен Telegram-бота
LLM_API_KEY	-- Ключ доступа к GigaChat API
LLM_MODEL_NAME	-- Название модели (например, GigaChat-Plus)
EMBEDDING_MODEL	-- Название модели эмбеддингов (all-MiniLM-L6-v2)
POSTGRES_DB, _USER, _PASSWORD, _HOST, _PORT	-- Подключение к PostgreSQL
REDIS_URL, CELERY_BROKER_URL, CELERY_RESULT_BACKEND	-- Подключение к Redis
QDRANT_URL	-- Адрес векторной БД Qdrant

Любые изменения в этих переменных требуют перезапуска бота и Celery.

## 5. Конвенции кода

### 5.1. Асинхронность

    Все обработчики aiogram — асинхронные (async def).

    Любые синхронные операции (ORM, PDF, вызовы API) оборачиваются в sync_to_async или выполняются через asyncio.to_thread.

    Не использовать time.sleep — только await asyncio.sleep.

### 5.2 5.2. Работа с Django ORM

    Модели описаны в core/models.py. Новые модели добавляются туда же с последующей миграцией.

    Сервисы (core/services/) инкапсулируют бизнес-логику и используют sync_to_async для вызовов ORM.

    Прямые обращения к моделям из обработчиков aiogram не рекомендуются — только через сервисы.

### 5.3. Обработка ошибок

    Внешние вызовы (LLM, Qdrant, Redis) всегда обёрнуты в try/except с логированием через logger.exception.

    Пользователю показывается дружелюбное сообщение, технические детали не раскрываются.

### 5.4. Логирование

    Используется стандартный модуль logging. Настройки в config/settings.py.

    Уровень логирования по умолчанию INFO.

    Все логи пишутся в stdout (видны в терминале).

## 6. Добавление новой команды (пошаговый пример)

Будет описан процесс добавления команды на примере /compare_summary:

    Создание или изменение клавиатуры в bot/keyboards/inline.py.

    Реализация обработчика в bot/handlers/summary.py.

    Добавление бизнес-логики в core/services/summary_service.py.

    (при необходимости) Добавление нового метода в LLMClient.

    Регистрация роутера (если новый файл) в bot/main.py.

## 7. Работа с промптами и LLM

    Все промпты лежат в core/utils/prompts/ в формате JSON.

    Каждый промпт содержит system_prompt и шаблон пользовательского запроса (user_prompt_template).

    Загрузка промптов выполняется в llm_client.py функцией _load_json.

    Класс LLMClient предоставляет методы для извлечения сущностей, генерации саммари, сравнения.

    При добавлении нового промпта нужно создать JSON-файл, загрузить его в llm_client.py и реализовать соответствующий метод.

## 8. Тестирование

    Тесты находятся в tests/, структура повторяет основные директории проекта.

    Используется pytest с плагинами pytest-asyncio, pytest-django, pytest-mock.

    База данных для тестов заменяется на SQLite in-memory (фикстура в conftest.py).

    Все внешние сервисы (LLM, Qdrant, Redis) мокаются.

    Для запуска всех тестов: pytest.

    Для запуска отдельного файла: pytest tests/bot/test_summary.py -v.