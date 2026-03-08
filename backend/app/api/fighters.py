import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import delete as sql_delete, func, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_db, require_admin
from app.db.models import (
    Agent,
    Bet,
    BetStatus,
    Fighter,
    FighterMatchupSavestate,
    Match,
    MatchStatus,
    User,
)
from app.exceptions import FighterNotFoundError, AgentNotFoundError, DuplicateFighterError, ValidationError
from app.schemas.fighter import FighterCreate, FighterOut, FighterUpdate
from app.services.image_validator import validate_image_file

router = APIRouter(prefix="/fighters", tags=["fighters"])

# Image storage directory
IMAGE_STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "fighters"
IMAGE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# Bayesian smoothing config for response-time fighter ranking.
# score = (wins + k*m) / (matches + k)
_RANK_PRIOR_MEAN = 0.5
_RANK_PRIOR_STRENGTH = 20.0


def _normalized_wins_matches(fighter: Fighter) -> tuple[int, int]:
    matches = max(int(fighter.matches_played or 0), 0)
    wins = max(int(fighter.matches_won or 0), 0)
    if wins > matches:
        wins = matches
    return wins, matches


def _raw_win_rate(fighter: Fighter) -> float:
    wins, matches = _normalized_wins_matches(fighter)
    if matches <= 0:
        return 0.0
    return wins / matches


def _smoothed_score(fighter: Fighter) -> float:
    wins, matches = _normalized_wins_matches(fighter)
    return (wins + (_RANK_PRIOR_STRENGTH * _RANK_PRIOR_MEAN)) / (
        matches + _RANK_PRIOR_STRENGTH
    )


def _rank_sort_key(fighter: Fighter) -> tuple[float, float, int, str]:
    wins, matches = _normalized_wins_matches(fighter)
    return (
        -_smoothed_score(fighter),
        -_raw_win_rate(fighter),
        -matches,
        (fighter.name or "").lower(),
    )


def _resolved_agent_architecture(fighter: Fighter) -> str | None:
    # Built-in architecture field takes priority.
    builtin_arch = (fighter.agent_architecture or "").strip()
    if builtin_arch:
        return builtin_arch

    # For custom agents, expose the linked agent architecture in API output.
    linked_arch = (
        (fighter.agent.architecture or "").strip()
        if getattr(fighter, "agent", None) is not None
        else ""
    )
    if linked_arch:
        return linked_arch
    return None


def _ranked_fighter_out(fighters: list[Fighter]) -> list[FighterOut]:
    ranked_rows = sorted(fighters, key=_rank_sort_key)
    out: list[FighterOut] = []
    for idx, fighter in enumerate(ranked_rows, start=1):
        out.append(
            FighterOut.model_validate(fighter).model_copy(
                update={
                    "rank": idx,
                    "agent_architecture": _resolved_agent_architecture(fighter),
                }
            )
        )
    return out


async def _get_ranked_fighters(db: AsyncSession) -> list[FighterOut]:
    result = await db.execute(select(Fighter).options(selectinload(Fighter.agent)))
    fighters = list(result.scalars().all())
    return _ranked_fighter_out(fighters)


async def _get_ranked_fighter_by_id(db: AsyncSession, fighter_id: UUID) -> FighterOut:
    ranked = await _get_ranked_fighters(db)
    for fighter in ranked:
        if fighter.id == fighter_id:
            return fighter
    raise FighterNotFoundError(str(fighter_id))


def _fighter_to_out(fighter: Fighter) -> FighterOut:
    """Serialize fighter with effective agent architecture resolved."""
    resolved_arch = fighter.agent.architecture if fighter.agent else fighter.agent_architecture
    out = FighterOut.model_validate(fighter)
    return out.model_copy(update={"agent_architecture": resolved_arch})


@router.get("/", response_model=list[FighterOut])
async def list_fighters(db: AsyncSession = Depends(get_db)):
    """List all fighters with their associated agents."""
    result = await db.execute(
        select(Fighter).options(selectinload(Fighter.agent)).order_by(Fighter.name)
    )
    fighters = result.scalars().all()
    return [_fighter_to_out(f) for f in fighters]


@router.get("/{fighter_id}", response_model=FighterOut)
async def get_fighter(fighter_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get fighter by ID with associated agent."""
    result = await db.execute(
        select(Fighter).where(Fighter.id == fighter_id).options(selectinload(Fighter.agent))
    )
    fighter = result.scalar_one_or_none()
    if fighter is None:
        raise FighterNotFoundError(str(fighter_id))
    return _fighter_to_out(fighter)


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
    if body.llm_model is not None:
        fighter.llm_model = body.llm_model
    if body.agent_architecture is not None:
        fighter.agent_architecture = body.agent_architecture
        fighter.agent_id = None  # Clear custom agent when using builtin
    if body.description is not None:
        fighter.description = body.description
    if body.origin is not None:
        fighter.origin = body.origin
    if body.special_move is not None:
        fighter.special_move = body.special_move
    if body.fight_style is not None:
        fighter.fight_style = body.fight_style
    if body.rank is not None:
        fighter.rank = body.rank

    await db.commit()
    await db.refresh(fighter, attribute_names=["agent"])

    return _fighter_to_out(fighter)



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

    # Null-out match references and remove dependent bets/matchup mappings first.
    await db.execute(
        sql_update(Match)
        .where(Match.fighter1_id == fighter_id)
        .values(fighter1_id=None)
    )
    await db.execute(
        sql_update(Match)
        .where(Match.fighter2_id == fighter_id)
        .values(fighter2_id=None)
    )
    await db.execute(
        sql_update(Match)
        .where(Match.winner_id == fighter_id)
        .values(winner_id=None)
    )
    await db.execute(sql_delete(Bet).where(Bet.fighter_id == fighter_id))
    await db.execute(
        sql_delete(FighterMatchupSavestate).where(
            or_(
                FighterMatchupSavestate.left_fighter_id == fighter_id,
                FighterMatchupSavestate.right_fighter_id == fighter_id,
            )
        )
    )
    await db.commit()

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

    return _fighter_to_out(fighter)


# ── Fighter Stats (computed from match/bet data) ──

@router.get("/{fighter_id}/stats")
async def get_fighter_stats(fighter_id: UUID, db: AsyncSession = Depends(get_db)):
    """Return comprehensive computed stats for a fighter."""
    # Verify fighter exists
    result = await db.execute(select(Fighter).where(Fighter.id == fighter_id))
    fighter = result.scalar_one_or_none()
    if not fighter:
        raise FighterNotFoundError(str(fighter_id))

    fid = fighter_id

    # All completed matches involving this fighter
    matches_q = await db.execute(
        select(Match)
        .where(
            Match.status == MatchStatus.COMPLETED,
            or_(Match.fighter1_id == fid, Match.fighter2_id == fid),
        )
    )
    all_matches = matches_q.scalars().all()

    # Basic counts
    total_played = len(all_matches)
    total_won = sum(1 for m in all_matches if m.winner_id == fid)

    # Side analysis: matches as P1 (fighter1) vs P2 (fighter2)
    as_p1 = [m for m in all_matches if m.fighter1_id == fid]
    as_p2 = [m for m in all_matches if m.fighter2_id == fid]
    p1_wins = sum(1 for m in as_p1 if m.winner_id == fid)
    p2_wins = sum(1 for m in as_p2 if m.winner_id == fid)
    p1_win_rate = round(p1_wins / len(as_p1), 4) if as_p1 else 0.0
    p2_win_rate = round(p2_wins / len(as_p2), 4) if as_p2 else 0.0

    # Flawless matches — won without the opponent winning a single round
    flawless = 0
    for m in all_matches:
        if m.winner_id != fid:
            continue
        if m.fighter1_id == fid and m.rounds_won_p2 == 0:
            flawless += 1
        elif m.fighter2_id == fid and m.rounds_won_p1 == 0:
            flawless += 1

    # Last match date
    completed_dates = [m.completed_at for m in all_matches if m.completed_at]
    last_match_date = max(completed_dates).isoformat() if completed_dates else None

    # Bet stats — total volume and total bets won on this fighter winning
    match_ids = [m.id for m in all_matches]
    bet_volume = 0.0
    bets_won_count = 0
    if match_ids:
        bets_q = await db.execute(
            select(Bet).where(Bet.match_id.in_(match_ids))
        )
        bets = bets_q.scalars().all()
        # Total volume = all bets on matches this fighter was in
        bet_volume = float(sum(b.amount for b in bets))
        # Bets won = bets placed ON this fighter that were won
        bets_won_count = sum(
            1 for b in bets
            if b.fighter_id == fid and b.status in (BetStatus.WON, BetStatus.CLAIMED)
        )

    return {
        "fighter_id": str(fid),
        "matches_played": total_played,
        "matches_won": total_won,
        "win_rate": round(total_won / total_played, 4) if total_played else 0.0,
        "p1_matches": len(as_p1),
        "p1_wins": p1_wins,
        "p1_win_rate": p1_win_rate,
        "p2_matches": len(as_p2),
        "p2_wins": p2_wins,
        "p2_win_rate": p2_win_rate,
        "flawless_matches": flawless,
        "total_bet_volume": round(bet_volume, 4),
        "total_bets_won": bets_won_count,
        "fighting_since": fighter.created_at.isoformat() if fighter.created_at else None,
        "last_match_date": last_match_date,
    }


@router.get("/{fighter_id}/matches")
async def get_fighter_matches(
    fighter_id: UUID,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Return recent match history for a fighter."""
    result = await db.execute(select(Fighter).where(Fighter.id == fighter_id))
    if not result.scalar_one_or_none():
        raise FighterNotFoundError(str(fighter_id))

    q = await db.execute(
        select(Match)
        .where(
            Match.status == MatchStatus.COMPLETED,
            or_(Match.fighter1_id == fighter_id, Match.fighter2_id == fighter_id),
        )
        .options(
            selectinload(Match.fighter1),
            selectinload(Match.fighter2),
            selectinload(Match.winner),
        )
        .order_by(Match.completed_at.desc())
        .limit(limit)
    )
    matches = q.scalars().all()

    # Aggregate bet amounts by match and side (P1/P2) for returned rows.
    bet_amounts_by_match: dict[UUID, dict[str, float]] = {}
    if matches:
        match_ids = [m.id for m in matches]
        match_by_id = {m.id: m for m in matches}
        bq = await db.execute(select(Bet).where(Bet.match_id.in_(match_ids)))
        for b in bq.scalars().all():
            match_row = match_by_id.get(b.match_id)
            if match_row is None:
                continue
            amount = float(b.amount or 0)
            bucket = bet_amounts_by_match.setdefault(
                b.match_id,
                {"total": 0.0, "p1": 0.0, "p2": 0.0},
            )
            bucket["total"] += amount
            if b.fighter_id == match_row.fighter1_id:
                bucket["p1"] += amount
            elif b.fighter_id == match_row.fighter2_id:
                bucket["p2"] += amount

    out = []
    for m in matches:
        is_p1 = m.fighter1_id == fighter_id
        opponent = m.fighter2 if is_p1 else m.fighter1
        won = m.winner_id == fighter_id
        rounds_for = m.rounds_won_p1 if is_p1 else m.rounds_won_p2
        rounds_against = m.rounds_won_p2 if is_p1 else m.rounds_won_p1
        amounts = bet_amounts_by_match.get(m.id, {"total": 0.0, "p1": 0.0, "p2": 0.0})
        bet_for_fighter = amounts["p1"] if is_p1 else amounts["p2"]
        bet_for_opponent = amounts["p2"] if is_p1 else amounts["p1"]
        out.append({
            "match_id": str(m.id),
            "opponent_id": str(opponent.id) if opponent else None,
            "opponent_name": opponent.name if opponent else "Unknown",
            "result": "WIN" if won else "LOSS",
            "rounds_won": rounds_for,
            "rounds_lost": rounds_against,
            "side": "P1" if is_p1 else "P2",
            "label": m.label,
            "completed_at": m.completed_at.isoformat() if m.completed_at else None,
            "total_bet_amount": round(amounts["total"], 6),
            "bet_amount_p1": round(amounts["p1"], 6),
            "bet_amount_p2": round(amounts["p2"], 6),
            "bet_amount_for_fighter": round(bet_for_fighter, 6),
            "bet_amount_for_opponent": round(bet_for_opponent, 6),
        })
    return out


@router.get("/{fighter_id}/vs/{opponent_id}")
async def get_fighter_vs(
    fighter_id: UUID,
    opponent_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Head-to-head stats between two fighters."""
    q = await db.execute(
        select(Match)
        .where(
            Match.status == MatchStatus.COMPLETED,
            or_(
                (Match.fighter1_id == fighter_id) & (Match.fighter2_id == opponent_id),
                (Match.fighter1_id == opponent_id) & (Match.fighter2_id == fighter_id),
            ),
        )
        .options(selectinload(Match.fighter1), selectinload(Match.fighter2))
        .order_by(Match.completed_at.desc())
    )
    matches = q.scalars().all()

    total = len(matches)
    wins = sum(1 for m in matches if m.winner_id == fighter_id)
    losses = total - wins

    return {
        "fighter_id": str(fighter_id),
        "opponent_id": str(opponent_id),
        "total_matches": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total, 4) if total else 0.0,
        "matches": [
            {
                "match_id": str(m.id),
                "result": "WIN" if m.winner_id == fighter_id else "LOSS",
                "completed_at": m.completed_at.isoformat() if m.completed_at else None,
            }
            for m in matches[:10]
        ],
    }
