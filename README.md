# Astro Vibe Bot

Telegram-бот для щоденних "вайбів" за знаками зодіаку та персональних відповідей, з підтримкою контексту новин.

## Можливості
- Щоденні вайби для кожного знаку зодіаку
- Персональні відповіді на запитання користувача
- Канальна розсилка для всіх 12 знаків
- Отримання новин з RSS або з Telegram-каналу (Telethon)

## Вимоги
- Python 3.11+
- Telegram Bot Token
- OpenAI API Key

## Встановлення
```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Конфігурація
Скопіюй приклад:
```
cp .env.example .env
```

Заповни `.env`:
- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `RSS_FEED_URL` (опціонально, якщо використовуєш Telegram-канал)
- `TIMEZONE` (наприклад, `Europe/Kyiv`)
- `BROADCAST_CHANNEL` (канал для щоденної розсилки)
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (для Telethon)
- `TELEGRAM_NEWS_CHANNEL` (канал новин)

Ознаки знаків: `config/signs.yaml`.

## Запуск
```
python main.py
```

## Перевірка у Telegram
1) `/start`
2) `/set_sign Овен`
3) Запитай будь-що (наприклад: “Чи варто інвестувати сьогодні?”)

## Ручна відправка в канал
Команда `/broadcast_now` відправляє повідомлення в канал одразу.
Рекомендується обмежити доступ через `ADMIN_USER_IDS` в `.env`.

## Канальна розсилка
Бот надсилає повідомлення о 09:00 за `TIMEZONE`.
Зроби бота адміном каналу, вказаного у `BROADCAST_CHANNEL`.

## Отримання новин з Telegram-каналу
Використовується Telethon і потрібен вхід під особистим аккаунтом.
Для продакшну рекомендується `TELETHON_SESSION_STRING` (StringSession), щоб не потрібен був інтерактивний логін.
Локально можна використовувати файл `telethon.session`.

## Структура модулів
Код розбито на модулі (раніше все було в `main.py`):
- `db.py` — робота з SQLite (користувачі, кеш денного контексту): `init_db`, `upsert_user`, `set_user_sign`, `get_user_sign`, `get_all_users`, `load_today_context`, `save_today_context`, `DB_PATH`.
- `news.py` — отримання новин: `fetch_telegram_messages`, `extract_invite_hash`, а також `fetch_news_blob()` (Telethon з фолбеком на RSS).
- `generation.py` — генерація через OpenAI (`AsyncOpenAI`): `generate_daily_context`, `get_or_generate_context`, `build_personal_prompt`, плюс хелпери з ретраями `complete_json` / `complete_text`.
- `telegram_io.py` — формат повідомлень і розсилка: `build_channel_sign_messages`, `broadcast_daily_vibes`, константи знаків (`SIGN_NAME_UA`, `SIGN_EMOJI`, ...), `load_signs`, `normalize_sign`, `display_sign`, `display_sign_with_emoji`.
- `main.py` — лише завантаження env, налаштування логування, реєстрація хендлерів, планувальник і точка входу.

## Промпти (`prompts/`)
Усі текстові промпти винесено в окремі файли — єдине джерело правди:
- `prompts/channel_system.txt` — системний промпт каналу (тон, ЗАБОРОНЕНО, основне правило).
- `prompts/intro.txt` — промпт для інтро/полірування глобального підсумку (плейсхолдери `{news_blob}`, `{raw_global_summary}`).
- `prompts/personal_advisor.txt` — системний промпт для персональних відповідей.

Завантаження: `from prompts.loader import load_prompt; load_prompt("channel_system")`.
Плейсхолдери заповнюються через `str.format(...)`. Щоб змінити голос/тон — редагуй відповідний `.txt`, код чіпати не треба.

## Надійність
- OpenAI-клієнт асинхронний (`AsyncOpenAI`), виклики не блокують event loop.
- Виклики обгорнуто ретраями (`tenacity`, до 3 спроб, експоненційна затримка).
- JSON вайбів валідовано: якщо знаків бракує — заповнюються фолбеком, з попередженням у лог.
- У розсилці кожне `send_message` ізольоване try/except — один заблокований користувач не зупиняє всю розсилку.
- Замість `print` використовується `logging` (кількість новин, латентність OpenAI, успіхи/збої розсилки).

## Локальні тести (Notebook)
- `test_bot.ipynb` — повний прохід: генерація контексту, прев'ю канальних повідомлень для 12 знаків, опційна відправка в чат/канал, пісочниця для персональних відповідей.
- `prompt_test.ipynb` — ізольована пісочниця для системного промпту каналу. Промпт завантажується з `prompts/channel_system.txt`; його можна перевизначити прямо в клітинці для експериментів.

Відкрий потрібний notebook та запусти клітинки послідовно. Обидва імпортують функції з нових модулів (`telegram_io`, `generation`, `news`).

