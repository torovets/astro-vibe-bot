"""Workstream B eval: generate sample days per tone variant + quality metrics.

Run:  python eval_prompts.py
Writes SAMPLES_B.md and prints a metrics summary. Uses fixed news fixtures so
results are reproducible without Telethon. Loads the API key from the main
checkout .env.
"""

import asyncio
import json
import statistics
from collections import Counter

from dotenv import load_dotenv

load_dotenv("/Users/tetianatorovets/repos/astro-vibe-bot/.env")

import os

from openai import AsyncOpenAI

from generation import _first_word, build_channel_system, generate_daily_context
from telegram_io import display_sign_with_emoji, load_signs

MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

NEWS_FIXTURES = {
    "День 1": (
        "- Курс гривні до долара трохи зміцнився після заяв Нацбанку.\n"
        "- У Києві комунальники достроково завершили ремонт ще однієї ділянки дороги.\n"
        "- Українська ІТ-компанія запустила застосунок для вивчення мов із ШІ.\n"
        "- Синоптики обіцяють різке потепління і перший по-справжньому літній тиждень.\n"
        "- Збірна України з футболу провела вдалий контрольний матч перед турніром."
    ),
    "День 2": (
        "- У «Дії» зʼявилася нова послуга — оформлення документів повністю онлайн.\n"
        "- Ціни на овочі нового врожаю почали падати на ринках.\n"
        "- Український фільм потрапив до програми великого європейського фестивалю.\n"
        "- Метеорологи попереджають про грози й короткі зливи на вихідних.\n"
        "- Стартап із Львова залучив інвестиції на розробку зелених технологій."
    ),
    "День 3": (
        "- Уряд анонсував програму підтримки малого бізнесу з пільговими кредитами.\n"
        "- У великих містах розширюють мережу велодоріжок.\n"
        "- Популярний стрімінг додав українську озвучку до нових серіалів.\n"
        "- Аграрії звітують про хороші прогнози на врожай зернових.\n"
        "- Молода українська тенісистка вийшла у наступне коло великого турніру."
    ),
}

TONES = ["sharp", "savage"]


def metrics_for(vibes: dict) -> dict:
    texts = [v for v in vibes.values() if v]
    firsts = [_first_word(t) for t in texts]
    today_openers = sum(1 for w in firsts if w == "сьогодні")
    distinct_ratio = len(set(firsts)) / len(firsts) if firsts else 0
    mean_len = statistics.mean(len(t) for t in texts) if texts else 0
    banned = [b for b in ("ретроград", "зірки кажуть", "планет") if any(b in t.lower() for t in texts)]
    return {
        "today_openers": today_openers,
        "distinct_first_word_ratio": round(distinct_ratio, 2),
        "mean_vibe_chars": round(mean_len),
        "banned_hits": banned,
        "most_common_first": Counter(firsts).most_common(1)[0] if firsts else ("", 0),
    }


async def judge_news_linkage(client, news_blob: str, vibes: dict) -> float:
    """Ask the model how many vibes connect to a news item. Returns % linked."""
    listing = "\n".join(f"{s}: {v}" for s, v in vibes.items())
    prompt = (
        "Нижче новини дня і 12 коротких вайбів. Для КОЖНОГО вайбу визнач, чи "
        "відчувається звʼязок із якоюсь подією з новин (так/ні). Поверни JSON "
        '{"links": [true/false x12]} у тому ж порядку.\n\n'
        f"Новини:\n{news_blob}\n\nВайби:\n{listing}"
    )
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    links = json.loads(resp.choices[0].message.content).get("links", [])
    return round(100 * sum(1 for x in links if x) / len(links)) if links else 0


async def main():
    from db import init_db

    init_db()
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    signs = load_signs()
    md = ["# Workstream B — зразки за тонами\n"]
    summary_rows = []

    for tone in TONES:
        md.append(f"\n## Тон: `{tone}`\n")
        md.append(f"> {build_channel_system(tone).splitlines()[3]}\n")
        for day, news in NEWS_FIXTURES.items():
            ctx = await generate_daily_context(
                client, signs, None, MODEL, tone=tone, news_blob=news
            )
            vibes = ctx["vibes"]
            m = metrics_for(vibes)
            linkage = await judge_news_linkage(client, news, vibes)
            summary_rows.append((tone, day, m, linkage))

            md.append(f"\n### {day}\n")
            md.append(f"**Інтро:** {ctx['global_summary']}\n")
            md.append(f"**Афірмація:** {ctx['affirmation']}\n")
            for sign in signs:
                md.append(f"- {display_sign_with_emoji(sign)}: {vibes.get(sign,'')}")
            md.append(
                f"\n_метрики: «Сьогодні»-початків={m['today_openers']}, "
                f"різноманіття перших слів={m['distinct_first_word_ratio']}, "
                f"сер. довжина={m['mean_vibe_chars']} символів, "
                f"звʼязок із новинами={linkage}%, "
                f"заборонені слова={m['banned_hits'] or 'немає'}_\n"
            )
            print(f"[{tone}] {day}: today={m['today_openers']} "
                  f"distinct={m['distinct_first_word_ratio']} "
                  f"len={m['mean_vibe_chars']} linkage={linkage}%")

    # Aggregate table
    md.append("\n## Підсумкова таблиця\n")
    md.append("| Тон | День | «Сьогодні» | Різноманіття | Сер. довжина | Звʼязок з новинами |")
    md.append("|---|---|---|---|---|---|")
    for tone, day, m, linkage in summary_rows:
        md.append(f"| {tone} | {day} | {m['today_openers']} | "
                  f"{m['distinct_first_word_ratio']} | {m['mean_vibe_chars']} | {linkage}% |")

    with open("SAMPLES_B.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\nWrote SAMPLES_B.md")


if __name__ == "__main__":
    asyncio.run(main())
