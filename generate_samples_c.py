"""Workstream C samples: 2 sign spotlights + 2 psychology hooks -> SAMPLES_C.md."""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv("/Users/tetianatorovets/repos/astro-vibe-bot/.env")

from openai import AsyncOpenAI

import rubrics
from db import init_db
from telegram_io import load_signs

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


async def main():
    init_db()
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    signs = load_signs()
    md = ["# Workstream C — зразки рубрик\n"]

    md.append("\n## Портрети знаків (щосереди 18:00)\n")
    for sign in ["Leo", "Virgo"]:
        body = await rubrics.generate_sign_spotlight(client, sign, signs[sign], MODEL)
        md.append(f"\n### {rubrics.spotlight_header(sign)}\n\n{body}\n")
        print(f"spotlight {sign}: {len(body)} chars")

    md.append("\n## Психологія стосунків (щосуботи 18:00)\n")
    topics = rubrics.load_psych_topics()
    for topic in [topics[0], topics[3]]:
        body = await rubrics.generate_psych_hook(client, topic, MODEL)
        md.append(f"\n### {rubrics.psych_header(topic)}\n\n{body}\n")
        print(f"hook '{topic}': {len(body)} chars")

    with open("SAMPLES_C.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\nWrote SAMPLES_C.md")


if __name__ == "__main__":
    asyncio.run(main())
