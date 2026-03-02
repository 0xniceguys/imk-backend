"""Seed initial fighter data into the database."""

import asyncio

from sqlalchemy import select

from app.db.engine import async_session, engine
from app.db.models import Fighter

FIGHTERS = [
    {
        "name": "SUB-ZERO",
        "slug": "sub-zero",
        "character": "Sub-Zero",
        "character_id": 0,
        "llm_model": "Claude Opus 4.6",
        "matches_played": 4151,
        "matches_won": 2574,
    },
    {
        "name": "SONIYA",
        "slug": "sonya",
        "character": "Sonya Blade",
        "character_id": 1,
        "llm_model": "ChatGPT 5.1 Codex",
        "matches_played": 4151,
        "matches_won": 415,
    },
    {
        "name": "SCORPION",
        "slug": "scorpion",
        "character": "Scorpion",
        "character_id": 2,
        "llm_model": "Gemini Ultra 2",
        "matches_played": 3200,
        "matches_won": 1440,
    },
    {
        "name": "JOHNNY CAGE",
        "slug": "johnny",
        "character": "Johnny Cage",
        "character_id": 3,
        "llm_model": "ChatGPT 5.1 Codex",
        "matches_played": 2800,
        "matches_won": 1540,
    },
    {
        "name": "RAIDEN",
        "slug": "raiden",
        "character": "Raiden",
        "character_id": 4,
        "llm_model": "Opus 4.6",
        "matches_played": 3500,
        "matches_won": 2485,
    },
]


async def seed():
    async with async_session() as db:
        for data in FIGHTERS:
            result = await db.execute(
                select(Fighter).where(Fighter.slug == data["slug"])
            )
            if result.scalar_one_or_none() is None:
                db.add(Fighter(**data))
                print(f"  + {data['name']}")
            else:
                print(f"  = {data['name']} (already exists)")

        await db.commit()
        print("Seed complete.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
