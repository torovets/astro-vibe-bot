# Astro Vibe Bot

Telegram-бот для щоденних "вайбів" за знаками зодіаку та персональних відповідей, з підтримкою контексту новин.

## Можливості
- Щоденні вайби для кожного знаку зодіаку (короткі, різноманітні, обігрують реальні новини)
- Персональні відповіді на запитання користувача
- Канальна розсилка для всіх 12 знаків + щоденна обкладинка-зображення
- Щотижневі рубрики: портрети знаків (ср 18:00) і психологія стосунків (сб 18:00)
- Отримання новин з RSS або з Telegram-каналу (Telethon)
- Два тони голосу (`CHANNEL_TONE=sharp|savage`)

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
- `CHANNEL_TONE` (опціонально: `savage` — на межі сарказму, або `sharp` — гостра іронія; дефолт `savage`)

Ознаки знаків: `config/signs.yaml` (риси, специфіка + `stereotype`/`love_style`/`money_style` для рубрики портретів).
Теми психологічної рубрики: `config/rubrics.yaml`.

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

Перед знаками публікується щоденна обкладинка-зображення: AI-фон (`gpt-image-1`,
фолбек `dall-e-3`) + український текст (афірмація + інтро), накладений локально
через Pillow (шрифт `assets/fonts/PT Sans`). Фон кешується в `daily_cover` по
даті. Якщо генерація падає — канал отримує звичайний текстовий інтро-блок
(розсилка ніколи не блокується). Деталі рендерингу: `render.py`.

## Щотижневі рубрики
- **Портрети знаків** — щосереди 18:00, по черзі всі 12 знаків без повторів.
- **Психологія стосунків** — щосуботи 18:00, по черзі теми з `config/rubrics.yaml`.

Ручний запуск (адмін): `/post_spotlight`, `/post_hook`. Ротація зберігається в
таблиці `rubric_history`; коли цикл завершується — починається заново. Логіка: `rubrics.py`.

## Тон голосу та зразки
- `CHANNEL_TONE` перемикає тон щоденних вайбів і рубрик.
- `python eval_prompts.py` — генерує по 3 дні на кожен тон + метрики (різноманіття
  початків, довжина, звʼязок із новинами) у `SAMPLES_B.md`.
- `python generate_samples_c.py` — зразки рубрик у `SAMPLES_C.md`.
- `python test_render.py` — реальні обкладинки в `samples/` + `SAMPLES_D.md`.

## Отримання новин з Telegram-каналу
Використовується Telethon і потрібен вхід під особистим аккаунтом.
Для продакшну рекомендується `TELETHON_SESSION_STRING` (StringSession), щоб не потрібен був інтерактивний логін.
Локально можна використовувати файл `telethon.session`.

## Структура модулів
Код розбито на модулі (раніше все було в `main.py`):
- `db.py` — робота з SQLite (користувачі, кеш денного контексту): `init_db`, `upsert_user`, `set_user_sign`, `get_user_sign`, `get_all_users`, `load_today_context`, `save_today_context`, `DB_PATH`.
- `news.py` — отримання новин: `fetch_telegram_messages`, `extract_invite_hash`, а також `fetch_news_blob()` (Telethon з фолбеком на RSS).
- `generation.py` — генерація через OpenAI (`AsyncOpenAI`): `generate_daily_context`, `get_or_generate_context`, `build_personal_prompt`, плюс хелпери з ретраями `complete_json` / `complete_text`.
- `telegram_io.py` — формат повідомлень і розсилка: `build_channel_sign_messages`, `broadcast_daily_vibes`, `send_daily_cover`, константи знаків (`SIGN_NAME_UA`, `SIGN_EMOJI`, ...), `load_signs`, `normalize_sign`, `display_sign`, `display_sign_with_emoji`.
- `rubrics.py` — щотижневі рубрики: `generate_sign_spotlight`, `generate_psych_hook`, `post_spotlight`, `post_hook`.
- `render.py` — обкладинки: `build_background_prompt`, `generate_background`, `render_card`.
- `main.py` — лише завантаження env, налаштування логування, реєстрація хендлерів, планувальник і точка входу.

## Промпти (`prompts/`)
Усі текстові промпти винесено в окремі файли — єдине джерело правди:
- `prompts/channel_system.txt` — системний промпт каналу (тон, ЗАБОРОНЕНО, основне правило, плейсхолдер `{tone_directive}` для A/B тонів).
- `prompts/intro.txt` — промпт для інтро/полірування глобального підсумку (плейсхолдери `{news_blob}`, `{raw_global_summary}`, `{yesterday_hint}`).
- `prompts/personal_advisor.txt` — системний промпт для персональних відповідей.
- `prompts/sign_spotlight.txt` — портрет знаку (рубрика).
- `prompts/psych_hook.txt` — психологія стосунків (рубрика).

Тон інʼєктується хелпером `generation.build_channel_system(tone)`, тож не завантажуй `channel_system` напряму через `load_prompt` (залишиться плейсхолдер `{tone_directive}`).

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

