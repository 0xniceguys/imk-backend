import shutil
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_db, require_admin
from app.db.models import Agent, Fighter, User
from app.exceptions import FighterNotFoundError, AgentNotFoundError, DuplicateFighterError, ValidationError
from app.schemas.fighter import FighterCreate, FighterOut, FighterUpdate
from app.services.image_validator import validate_image_file

router = APIRouter(prefix="/fighters", tags=["fighters"])

# Image storage directory
IMAGE_STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "fighters"
IMAGE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/", response_model=list[FighterOut])
async def list_fighters(db: AsyncSession = Depends(get_db)):
    """List all fighters with their associated agents."""
    result = await db.execute(
        select(Fighter).options(selectinload(Fighter.agent)).order_by(Fighter.name)
    )
    return result.scalars().all()


@router.get("/{fighter_id}", response_model=FighterOut)
async def get_fighter(fighter_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get fighter by ID with associated agent."""
    result = await db.execute(
        select(Fighter).where(Fighter.id == fighter_id).options(selectinload(Fighter.agent))
    )
    fighter = result.scalar_one_or_none()
    if fighter is None:
        raise FighterNotFoundError(str(fighter_id))
    return fighter


@router.put("/{fighter_id}", response_model=FighterOut)
async def update_fighter(
    fighter_id: UUID,
    body: FighterUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update fighter (name, image, agent, etc.). Admin only."""
    result = await db.execute(
        select(Fighter).where(Fighter.id == fighter_id).options(selectinload(Fighter.agent))
    )
    fighter = result.scalar_one_or_none()
    if fighter is None:
        raise FighterNotFoundError(str(fighter_id))

    # Validate agent_id if provided
    if body.agent_id is not None:
        agent_result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
        if agent_result.scalar_one_or_none() is None:
            raise AgentNotFoundError(str(body.agent_id))
        fighter.agent_id = body.agent_id
        fighter.agent_architecture = None  # Clear builtin when using custom agent

    # Update fields
    if body.name is not None:
        fighter.name = body.name
    if body.image_url is not None:
        fighter.image_url = body.image_url
    if body.agent_architecture is not None:
        fighter.agent_architecture = body.agent_architecture
        fighter.agent_id = None  # Clear custom agent when using builtin

    await db.commit()
    await db.refresh(fighter, attribute_names=["agent"])

    return fighter


@router.delete("/{fighter_id}")
async def delete_fighter(
    fighter_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a fighter. Admin only."""
    result = await db.execute(select(Fighter).where(Fighter.id == fighter_id))
    fighter = result.scalar_one_or_none()
    if fighter is None:
        raise FighterNotFoundError(str(fighter_id))

    await db.delete(fighter)
    await db.commit()

    return {"status": "deleted", "fighter_id": str(fighter_id)}


@router.post("/{fighter_id}/image", response_model=FighterOut)
async def upload_fighter_image(
    fighter_id: UUID,
    image: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload an image for a fighter. Admin only.

    Accepts JPEG, PNG, or WEBP images up to 5 MB.
    Maximum dimensions: 2048x2048 pixels.
    Minimum dimensions: 100x100 pixels.
    """
    # Check fighter exists
    result = await db.execute(
        select(Fighter).where(Fighter.id == fighter_id).options(selectinload(Fighter.agent))
    )
    fighter = result.scalar_one_or_none()
    if fighter is None:
        raise FighterNotFoundError(str(fighter_id))

    # Validate image file
    if not image.filename:
        raise ValidationError("No filename provided")

    try:
        metadata = validate_image_file(image.file, image.filename)
    except Exception as e:
        raise ValidationError(f"Invalid image: {e}")

    # Generate filename: {fighter_slug}.{ext}
    ext = Path(image.filename).suffix.lower()
    filename = f"{fighter.slug}{ext}"
    file_path = IMAGE_STORAGE_DIR / filename

    # Save file
    image.file.seek(0)  # Reset file pointer after validation
    try:
        with file_path.open("wb") as f:
            shutil.copyfileobj(image.file, f)
    except Exception as e:
        raise ValidationError(f"Failed to save image: {e}")

    # Update fighter's image_url
    fighter.image_url = f"/uploads/fighters/{filename}"
    await db.commit()
    await db.refresh(fighter, attribute_names=["agent"])

    return fighter
