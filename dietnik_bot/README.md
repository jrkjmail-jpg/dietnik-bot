# Dietnik Bot

Dietnik — Telegram-бот диетолог. Пользователь отправляет фото еды, бот анализирует его через OpenAI Vision, считает КБЖУ, сохраняет приём пищи и показывает дневной прогресс.

## Что умеет

- Анкета пользователя через `/start`
- Расчёт дневной нормы КБЖУ по формуле Mifflin-St Jeor
- Анализ фото еды через OpenAI
- Сохранение приёмов пищи в SQLite
- Прогресс за день через `/today`
- Профиль через `/profile`
- Сброс сегодняшнего дневника через `/reset_day`

## Установка Python на Mac

1. Открой Terminal.
2. Проверь Python:

```bash
python3 --version
```

3. Если Python не установлен, установи его с сайта [python.org](https://www.python.org/downloads/macos/) или через Homebrew:

```bash
brew install python
```

## Установка проекта

Перейди в папку проекта:

```bash
cd dietnik_bot
```

Создай виртуальное окружение:

```bash
python3 -m venv .venv
```

Активируй его:

```bash
source .venv/bin/activate
```

Установи зависимости:

```bash
pip install -r requirements.txt
```

## Настройка .env

Создай файл `.env` рядом с `main.py`:

```bash
cp .env.example .env
```

Открой `.env` и вставь токены:

```env
BOT_TOKEN=твой_telegram_bot_token
OPENAI_API_KEY=твой_openai_api_key
```

### Где взять Telegram bot token

1. Открой Telegram.
2. Найди бота `@BotFather`.
3. Отправь команду `/newbot`.
4. Придумай имя и username бота.
5. BotFather выдаст токен. Вставь его в `BOT_TOKEN`.

### Где взять OpenAI API key

1. Открой [platform.openai.com](https://platform.openai.com/).
2. Перейди в раздел API keys.
3. Создай новый ключ.
4. Вставь его в `OPENAI_API_KEY`.

## Запуск

Из папки `dietnik_bot` выполни:

```bash
python3 main.py
```

После запуска открой своего Telegram-бота и отправь `/start`.

## Команды

- `/start` — начать настройку заново
- `/profile` — показать профиль и дневную норму
- `/today` — показать прогресс за сегодня
- `/reset_day` — удалить сегодняшние приёмы пищи
- `/help` — показать инструкцию

## Важно

Оценка еды по фото может отличаться от реальности. Для максимальной точности используй кухонные весы и проверяй размер порций.

Не храни реальные токены в коде и не публикуй файл `.env`.

## Структура проекта

```text
dietnik_bot/
  main.py
  config.py
  database.py
  nutrition.py
  openai_service.py
  keyboards.py
  requirements.txt
  .env.example
  README.md
```

## Планы

### А) Холодильник

- Пользователь добавляет продукты
- Бот хранит продукты
- Рецепты подбираются из холодильника

### Б) Рецепты

- Рецепты подбираются под дневной остаток КБЖУ
- У рецепта есть процент совпадения с целью
- Бот учитывает цель пользователя и остаток калорий, белков, жиров и углеводов

### Другие идеи

- Статистика за неделю
- История приёмов пищи
- Платная подписка
- Персональные рекомендации
