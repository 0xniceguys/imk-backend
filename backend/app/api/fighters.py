from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.db.models import Fighter
from app.schemas.fighter import FighterOut

router = APIRouter(prefix="/fighters", tags=["fighters"])


@router.get("/", response_model=list[FighterOut])
async def list_fighters(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Fighter).order_by(Fighter.name))
    return result.scalars().all()


@router.get("/{fighter_id}", response_model=FighterOut)
async def get_fighter(fighter_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Fighter).where(Fighter.id == fighter_id))
    fighter = result.scalar_one_or_none()
    if fighter is None:
        raise HTTPException(404, "Fighter not found")
    return fighter
