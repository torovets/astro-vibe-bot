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

## Канальна розсилка
Бот надсилає повідомлення о 08:00 за `TIMEZONE`.
Зроби бота адміном каналу, вказаного у `BROADCAST_CHANNEL`.

## Отримання новин з Telegram-каналу
Використовується Telethon і потрібен вхід під особистим аккаунтом.
Для продакшну рекомендується `TELETHON_SESSION_STRING` (StringSession), щоб не потрібен був інтерактивний логін.
Локально можна використовувати файл `telethon.session`.

## Локальні тести (Notebook)
Відкрий `test_bot.ipynb` та запусти клітинки послідовно.

