"""Workstream D: generate real backgrounds + render cover variants -> samples/."""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv("/Users/tetianatorovets/repos/astro-vibe-bot/.env")

from openai import AsyncOpenAI

import render

AFFIRMATION = "Літо стукає у вікно — впусти його"
INTRO = (
    "Гривня трохи зміцніла, дороги відкриваються, а синоптики обіцяють перший "
    "по-справжньому теплий тиждень. Ідеальний день, щоб згадати, що ґрунт під "
    "ногами буває і твердим."
)
LONG_VIBE = (
    "Терези сьогодні балансують між двома кафе так, ніби вирішують долю людства, "
    "а тим часом курс гривні нагадує: іноді найкраще рішення — просто обрати і не "
    "озиратися на втрачені альтернативи, бо естетика теж буває практичною."
)


async def main():
    os.makedirs("samples", exist_ok=True)
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    print("Generating background 1...")
    bg1 = await render.generate_background(client, render.build_background_prompt(INTRO))
    print("Generating background 2...")
    bg2 = await render.generate_background(
        client, render.build_background_prompt(INTRO, mood_hint="warm summer dawn")
    )

    variants = [
        ("cover_1.png", bg1, dict(scrim_opacity=150, body_size=56, text_anchor="bottom")),
        ("cover_2.png", bg1, dict(scrim_opacity=110, body_size=52, text_anchor="center")),
        ("cover_3.png", bg2, dict(scrim_opacity=170, body_size=60, text_anchor="bottom")),
    ]
    for name, bg, opts in variants:
        buf = render.render_card(AFFIRMATION, INTRO, bg, **opts)
        with open(f"samples/{name}", "wb") as f:
            f.write(buf.getvalue())
        print(f"wrote samples/{name} ({len(buf.getvalue())//1024} KB) opts={opts}")

    # Overflow / wrap stress test with the longest realistic text.
    buf = render.render_card(AFFIRMATION, LONG_VIBE, bg1, body_size=56)
    with open("samples/cover_long.png", "wb") as f:
        f.write(buf.getvalue())
    print("wrote samples/cover_long.png (overflow test)")


if __name__ == "__main__":
    asyncio.run(main())
