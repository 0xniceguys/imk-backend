"""Agent management API endpoints."""

import logging
import shutil
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, require_admin
from app.db.models import Agent, User
from app.exceptions import AgentNotFoundError, ValidationError, DuplicateFighterError
from app.schemas.agent import AgentCreate, AgentOut, AgentUpdate
from app.services.agent_validator import validate_onnx_checkpoint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])

# Agent checkpoint storage directory
AGENT_STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "agent_storage"
AGENT_STORAGE_DIR.mkdir(exist_ok=True)


@router.get("/", response_model=list[AgentOut])
async def list_agents(
    db: AsyncSession = Depends(get_db),
    public_only: bool = True,
):
    """List all agents (public by default)."""
    query = select(Agent).order_by(Agent.created_at.desc())
    if public_only:
        query = query.where(Agent.is_public == True)  # noqa: E712
    result = await db.execute(query)
    agents = result.scalars().all()
    return agents


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get agent by ID."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AgentNotFoundError(str(agent_id))
    return agent


@router.post("/", response_model=AgentOut)
async def upload_agent(
    name: str = Form(...),
    slug: str = Form(...),
    architecture: str = Form(...),
    description: str | None = Form(None),
    is_public: bool = Form(True),
    checkpoint_file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload a new agent checkpoint (ONNX file).

    Requires:
    - name: Display name (3-100 chars)
    - slug: URL-safe identifier (3-50 chars, lowercase, hyphens/underscores only)
    - architecture: One of: lstm, transformer, disc_rssm, obj_belief
    - checkpoint_file: ONNX file (max 100 MB)
    - description: Optional description (max 1000 chars)
    - is_public: Whether agent is publicly visible (default: true)
    """
    # Validate agent creation data
    agent_data = AgentCreate(
        name=name,
        slug=slug,
        architecture=architecture,
        description=description,
        is_public=is_public,
    )

    # Check for duplicate name or slug
    result = await db.execute(
        select(Agent).where((Agent.name == agent_data.name) | (Agent.slug == agent_data.slug))
    )
    existing = result.scalar_one_or_none()
    if existing:
        if existing.name == agent_data.name:
            raise DuplicateFighterError("name", agent_data.name)
        raise DuplicateFighterError("slug", agent_data.slug)

    # Validate file is ONNX
    if not checkpoint_file.filename or not checkpoint_file.filename.endswith(".onnx"):
        raise ValidationError("Checkpoint file must be an ONNX file (.onnx extension)")

    # Save uploaded file temporarily
    temp_path = AGENT_STORAGE_DIR / f"temp_{agent_data.slug}.onnx"
    try:
        with temp_path.open("wb") as f:
            shutil.copyfileobj(checkpoint_file.file, f)

        # Validate ONNX checkpoint
        metadata = validate_onnx_checkpoint(temp_path, agent_data.architecture)

        # Move to permanent location
        final_path = AGENT_STORAGE_DIR / f"{agent_data.slug}.onnx"
        if final_path.exists():
            raise ValidationError(f"Agent checkpoint already exists: {agent_data.slug}.onnx")
        temp_path.rename(final_path)

        # Create Agent record
        agent = Agent(
            name=agent_data.name,
            slug=agent_data.slug,
            architecture=agent_data.architecture,
            description=agent_data.description,
            checkpoint_path=str(final_path),
            file_size_bytes=metadata["file_size_bytes"],
            uploaded_by=admin.id,
            is_public=agent_data.is_public,
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)

        logger.info(
            f"✓ Agent uploaded: {agent.name} ({agent.architecture})",
            extra={
                "agent_id": str(agent.id),
                "slug": agent.slug,
                "architecture": agent.architecture,
                "file_size_mb": round(agent.file_size_bytes / 1024 / 1024, 2),
                "uploaded_by": str(admin.id),
            },
        )

        return agent

    except Exception as e:
        # Clean up temp file on error
        if temp_path.exists():
            temp_path.unlink()
        raise


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: UUID,
    body: AgentUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update agent metadata (name, description, is_public)."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AgentNotFoundError(str(agent_id))

    # Update fields
    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.is_public is not None:
        agent.is_public = body.is_public

    await db.commit()
    await db.refresh(agent)

    logger.info(
        f"✓ Agent updated: {agent.name}",
        extra={"agent_id": str(agent.id), "updated_by": str(admin.id)},
    )

    return agent


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an agent and its checkpoint file."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AgentNotFoundError(str(agent_id))

    # Delete checkpoint file
    checkpoint_path = Path(agent.checkpoint_path)
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        logger.info(f"Deleted checkpoint file: {checkpoint_path}")

    # Delete DB record
    await db.delete(agent)
    await db.commit()

    logger.info(
        f"✓ Agent deleted: {agent.name}",
        extra={"agent_id": str(agent.id), "deleted_by": str(admin.id)},
    )

    return {"status": "deleted", "agent_id": str(agent_id)}
