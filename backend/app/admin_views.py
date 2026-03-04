"""
Admin panel — server-rendered HTML views using Jinja2 templates.

All routes under /admin/ are protected by a simple password cookie.
Set ADMIN_PASSWORD env var (defaults to "admin" in dev).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import get_available_agents
from app.dependencies import get_db
from app.db.models import (
    Bet,
    BetStatus,
    Fighter,
    Match,
    MatchStatus,
    Stream,
    StreamStatus,
)
from app.services.emulator import M64P_ROOT
from app.services.actions import decode_controller_state
from app.services.match_runner import get_all_runners, get_runner

router = APIRouter(prefix="/admin", tags=["admin-views"])

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
_COOKIE_NAME = "imk_admin"


def _is_admin_authed(request: Request) -> bool:
    """Check if the admin cookie matches the password."""
    return request.cookies.get(_COOKIE_NAME) == ADMIN_PASSWORD


def _require_admin(request: Request) -> RedirectResponse | None:
    """Return a redirect to /admin/login if not authed, else None."""
    if not _is_admin_authed(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return None


def _require_admin_api(request: Request) -> None:
    if not _is_admin_authed(request):
        raise HTTPException(status_code=401, detail="Admin auth required")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Savestate discovery — scan once at import, rescan on demand
REPO_ROOT = Path(__file__).resolve().parents[2]
SAVESTATE_DIRS = [
    REPO_ROOT / "training" / "data" / "savestates",
    M64P_ROOT / "data" / "savestates",
]


def _discover_savestates() -> list[dict]:
    """Find all .st files across known savestate directories."""
    results = []
    for base in SAVESTATE_DIRS:
        if not base.exists():
            continue
        for st_path in sorted(base.rglob("*.st")):
            rel = st_path.relative_to(REPO_ROOT) if st_path.is_relative_to(REPO_ROOT) else st_path
            results.append({
                "path": str(st_path),
                "name": str(rel),
            })
    return results


# ── Helpers ──

async def _get_db():
    """Inline db session for views (avoids Depends in non-API routes)."""
    from app.db.engine import async_session
    async with async_session() as session:
        yield session


# ── Auth routes (no gate) ──

@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if _is_admin_authed(request):
        return RedirectResponse(url="/admin/", status_code=303)
    return templates.TemplateResponse("admin_login.html", {
        "request": request,
        "error": None,
    })


@router.post("/login")
async def admin_login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        resp = RedirectResponse(url="/admin/", status_code=303)
        resp.set_cookie(
            _COOKIE_NAME, password,
            httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7,
        )
        return resp
    return templates.TemplateResponse("admin_login.html", {
        "request": request,
        "error": "Invalid password.",
    })


@router.get("/logout")
async def admin_logout():
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie(_COOKIE_NAME)
    return resp


# ── Debug / diagnostics ──

@router.get("/debug", response_class=HTMLResponse)
async def debug_display(request: Request):
    """Show all active runners and their display/FFmpeg/ctrl status."""
    if r := _require_admin(request): return r
    runners = get_all_runners()
    info = []
    for mid, r in runners.items():
        info.append({
            "match_id": mid,
            "instance_id": r.instance_id,
            "state": r.state.value,
            "display": r._session.display if r._session else "—",
            "ctrl_p1": r._ctrl_p1_path or "—",
            "ctrl_p2": r._ctrl_p2_path or "—",
            "ffmpeg_running": r._frame_capture._running if r._frame_capture else False,
            "latest_frame_kb": round(len(r.latest_frame) / 1024, 1) if r.latest_frame else 0,
            "viewer_url": f"/admin/viewer/{mid}",
        })
    return templates.TemplateResponse("debug.html", {
        "request": request,
        "active_page": "debug",
        "runners": info,
    })


# ── Live Viewer ──

@router.get("/viewer", response_class=HTMLResponse)
async def viewer_no_match(request: Request):
    """Viewer page with no match pre-selected — user picks from dropdown."""
    if r := _require_admin(request): return r
    return templates.TemplateResponse("test_viewer.html", {
        "request": request,
        "active_page": "viewer",
        "match_id": "",
        "savestates": _discover_savestates(),
    })


@router.get("/viewer/{match_id}", response_class=HTMLResponse)
async def viewer_with_match(request: Request, match_id: str):
    """Viewer page with a specific match pre-selected."""
    if r := _require_admin(request): return r
    return templates.TemplateResponse("test_viewer.html", {
        "request": request,
        "active_page": "viewer",
        "match_id": match_id,
        "savestates": _discover_savestates(),
    })


@router.post("/viewer/control/{match_id}/start")
async def viewer_control_start(request: Request, match_id: str):
    _require_admin_api(request)
    await match_start(UUID(match_id))
    runner = get_runner(match_id)
    return JSONResponse({
        "ok": runner is not None,
        "match_id": match_id,
        "runner_state": runner.state.value if runner else None,
    })


@router.post("/viewer/control/{match_id}/stop")
async def viewer_control_stop(request: Request, match_id: str):
    _require_admin_api(request)
    await match_stop(UUID(match_id))
    runner = get_runner(match_id)
    return JSONResponse({
        "ok": runner is None,
        "match_id": match_id,
    })


@router.post("/viewer/control/{match_id}/load-savestate")
async def viewer_control_load_savestate(request: Request, match_id: str):
    _require_admin_api(request)
    runner = get_runner(match_id)
    if not runner:
        raise HTTPException(status_code=404, detail="No active runner for match")
    payload = await request.json()
    savestate_path = str(payload.get("savestate_path") or "").strip() or None
    result = await runner.debug_load_savestate(savestate_path=savestate_path)
    return JSONResponse(result)


@router.post("/viewer/control/{match_id}/manual-mode")
async def viewer_control_manual_mode(request: Request, match_id: str):
    _require_admin_api(request)
    runner = get_runner(match_id)
    if not runner:
        raise HTTPException(status_code=404, detail="No active runner for match")
    payload = await request.json()
    player = int(payload.get("player", 0))
    enabled = bool(payload.get("enabled", False))
    if player not in (1, 2):
        raise HTTPException(status_code=400, detail="player must be 1 or 2")
    state = await runner.set_manual_mode(player, enabled)
    return JSONResponse({"ok": True, "manual_control": state})


@router.post("/viewer/control/{match_id}/controller")
async def viewer_control_controller(request: Request, match_id: str):
    _require_admin_api(request)
    runner = get_runner(match_id)
    if not runner:
        raise HTTPException(status_code=404, detail="No active runner for match")
    payload = await request.json()
    player = int(payload.get("player", 0))
    if player not in (1, 2):
        raise HTTPException(status_code=400, detail="player must be 1 or 2")
    controller_state = decode_controller_state(payload)
    manual_state = await runner.set_manual_controller_state(
        player,
        controller_state,
        enable=bool(payload.get("enabled", True)),
    )
    return JSONResponse({"ok": True, "manual_control": manual_state})


@router.post("/viewer/control/{match_id}/release")
async def viewer_control_release(request: Request, match_id: str):
    _require_admin_api(request)
    runner = get_runner(match_id)
    if not runner:
        raise HTTPException(status_code=404, detail="No active runner for match")
    payload = await request.json()
    player_raw = payload.get("player")
    player = int(player_raw) if player_raw not in (None, "") else None
    if player is not None and player not in (1, 2):
        raise HTTPException(status_code=400, detail="player must be 1 or 2")
    manual_state = await runner.release_manual_controls(
        player,
        disable=bool(payload.get("disable", False)),
    )
    return JSONResponse({"ok": True, "manual_control": manual_state})


# ── Dashboard ──

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if r := _require_admin(request): return r
    async for db in _get_db():
        # Counts
        live_r = await db.execute(
            select(Match).where(Match.status == MatchStatus.LIVE)
            .options(selectinload(Match.fighter1), selectinload(Match.fighter2))
        )
        live_matches = list(live_r.scalars().all())

        upcoming_r = await db.execute(
            select(Match).where(Match.status == MatchStatus.UPCOMING)
            .options(selectinload(Match.fighter1), selectinload(Match.fighter2))
            .order_by(Match.scheduled_at)
        )
        upcoming_matches = list(upcoming_r.scalars().all())

        completed_r = await db.execute(
            select(Match).where(Match.status == MatchStatus.COMPLETED)
            .options(selectinload(Match.fighter1), selectinload(Match.fighter2), selectinload(Match.winner))
            .order_by(Match.completed_at.desc()).limit(10)
        )
        recent_matches = list(completed_r.scalars().all())

        completed_count_r = await db.execute(
            select(Match).where(Match.status == MatchStatus.COMPLETED)
        )
        completed_count = len(list(completed_count_r.scalars().all()))

        # Attach runner snapshots to live matches
        runners = get_all_runners()
        for m in live_matches:
            runner = runners.get(str(m.id))
            if runner:
                m.runner = runner.latest_snapshot
            else:
                m.runner = None

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "active_page": "dashboard",
            "live_matches": live_matches,
            "live_count": len(live_matches),
            "upcoming_matches": upcoming_matches,
            "upcoming_count": len(upcoming_matches),
            "recent_matches": recent_matches,
            "completed_count": completed_count,
        })


# ── Matches list ──

@router.get("/matches", response_class=HTMLResponse)
async def matches_list(request: Request):
    if r := _require_admin(request): return r
    async for db in _get_db():
        result = await db.execute(
            select(Match)
            .options(
                selectinload(Match.fighter1),
                selectinload(Match.fighter2),
                selectinload(Match.winner),
            )
            .order_by(Match.created_at.desc())
        )
        matches = list(result.scalars().all())

        return templates.TemplateResponse("matches.html", {
            "request": request,
            "active_page": "matches",
            "matches": matches,
            "flash": None,
        })


# ── New match form ──

@router.get("/matches/new", response_class=HTMLResponse)
async def match_new_form(request: Request):
    if r := _require_admin(request): return r
    return templates.TemplateResponse("match_new.html", {
        "request": request,
        "active_page": "matches",
        "agents": get_available_agents(),
        "savestates": _discover_savestates(),
        "error": None,
    })


@router.post("/matches/new", response_class=HTMLResponse)
async def match_new_submit(
    request: Request,
    p1_agent: str = Form(...),
    p2_agent: str = Form(...),
    label: str = Form("MK4-Classic"),
    savestate_path: str = Form(...),
    best_of: int = Form(3),
):
    async for db in _get_db():
        # Resolve agent display names
        from app.agents import discover_agents
        agent_map = {a.id: a for a in discover_agents()}
        p1_info = agent_map.get(p1_agent)
        p2_info = agent_map.get(p2_agent)
        p1_name = p1_info.name if p1_info else p1_agent
        p2_name = p2_info.name if p2_info else p2_agent

        # Find or create fighter records for each agent so bets + settlement work.
        # Slug = agent_id, character = "MK4", character_id = 0 (generic).
        async def _ensure_fighter(agent_id: str, display_name: str) -> Fighter:
            r = await db.execute(select(Fighter).where(Fighter.slug == agent_id))
            existing = r.scalar_one_or_none()
            if existing:
                return existing
            f = Fighter(
                name=display_name,
                slug=agent_id,
                character="MK4",
                character_id=0,
                llm_model=agent_id,
                agent_architecture=agent_id,
            )
            db.add(f)
            await db.flush()
            return f

        f1 = await _ensure_fighter(p1_agent, p1_name)
        f2 = await _ensure_fighter(p2_agent, p2_name)

        match = Match(
            fighter1_id=f1.id,
            fighter2_id=f2.id,
            p1_agent=p1_agent,
            p2_agent=p2_agent,
            scheduled_at=datetime.now(timezone.utc),
            label=label,
            savestate_path=savestate_path,
            best_of=best_of,
        )
        db.add(match)
        await db.flush()
        stream = Stream(match_id=match.id)
        db.add(stream)
        await db.commit()

        return RedirectResponse(url=f"/admin/matches/{match.id}", status_code=303)


# ── Match detail ──

@router.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_detail(request: Request, match_id: UUID):
    if r := _require_admin(request): return r
    async for db in _get_db():
        result = await db.execute(
            select(Match).where(Match.id == match_id)
            .options(
                selectinload(Match.fighter1),
                selectinload(Match.fighter2),
                selectinload(Match.winner),
                selectinload(Match.bets).selectinload(Bet.user),
                selectinload(Match.bets).selectinload(Bet.fighter),
            )
        )
        match = result.scalar_one_or_none()
        if not match:
            return HTMLResponse("Match not found", status_code=404)

        runner = get_runner(str(match_id))
        runner_snapshot = runner.latest_snapshot if runner else None

        # Build agent display info
        from app.agents import discover_agents
        agent_map = {a.id: a for a in discover_agents()}
        p1_info = agent_map.get(match.p1_agent)
        p2_info = agent_map.get(match.p2_agent)

        return templates.TemplateResponse("match_detail.html", {
            "request": request,
            "active_page": "matches",
            "match": match,
            "bets": match.bets,
            "runner": runner_snapshot,
            "runner_state": runner.state.value if runner else None,
            "p1_agent_name": p1_info.name if p1_info else match.p1_agent,
            "p2_agent_name": p2_info.name if p2_info else match.p2_agent,
            "flash": None,
        })


# ── Match actions (form POSTs → redirect) ──

@router.post("/matches/{match_id}/start")
async def match_start(match_id: UUID):
    async for db in _get_db():
        # ✅ FIX: Eager load fighters and their agents for checkpoint path resolution
        result = await db.execute(
            select(Match).where(Match.id == match_id).options(
                selectinload(Match.stream),
                selectinload(Match.fighter1).selectinload(Fighter.agent),
                selectinload(Match.fighter2).selectinload(Fighter.agent),
            )
        )
        match = result.scalar_one_or_none()
        if not match or match.status != MatchStatus.UPCOMING:
            return RedirectResponse(url=f"/admin/matches/{match_id}", status_code=303)

        if not match.savestate_path:
            return RedirectResponse(url=f"/admin/matches/{match_id}", status_code=303)

        # ✅ FIX: Wire uploaded agents into match execution (same logic as JSON API)
        p1_agent_id = match.p1_agent
        p2_agent_id = match.p2_agent
        p1_checkpoint_path: str | None = None
        p2_checkpoint_path: str | None = None
        p1_architecture: str | None = None
        p2_architecture: str | None = None

        if match.fighter1:
            if match.fighter1.agent_id and match.fighter1.agent:
                p1_agent_id = f"custom_{match.fighter1.agent.slug}"
                p1_checkpoint_path = match.fighter1.agent.checkpoint_path
                p1_architecture = match.fighter1.agent.architecture
            elif match.fighter1.agent_architecture in ("random", "cpu", "lstm", "obj_belief", "disc_rssm", "transformer"):
                p1_agent_id = match.fighter1.agent_architecture
            # else: keep default "random"

        if match.fighter2:
            if match.fighter2.agent_id and match.fighter2.agent:
                p2_agent_id = f"custom_{match.fighter2.agent.slug}"
                p2_checkpoint_path = match.fighter2.agent.checkpoint_path
                p2_architecture = match.fighter2.agent.architecture
            elif match.fighter2.agent_architecture in ("random", "cpu", "lstm", "obj_belief", "disc_rssm", "transformer"):
                p2_agent_id = match.fighter2.agent_architecture
            # else: keep default "random"

        match.status = MatchStatus.LIVE
        match.started_at = datetime.now(timezone.utc)
        if match.stream:
            match.stream.status = StreamStatus.STARTING
        await db.commit()

        from app.services.match_runner import start_match as runner_start
        try:
            await runner_start(
                match_id=str(match_id),
                savestate_path=match.savestate_path,
                p1_agent_id=p1_agent_id,
                p2_agent_id=p2_agent_id,
                p1_checkpoint_path=p1_checkpoint_path,
                p2_checkpoint_path=p2_checkpoint_path,
                p1_architecture=p1_architecture,
                p2_architecture=p2_architecture,
                best_of=match.best_of,
            )
            if match.stream:
                match.stream.status = StreamStatus.LIVE
                await db.commit()
        except Exception as exc:
            import traceback
            import logging as _logging
            _logging.getLogger(__name__).error(
                "match_start FAILED for %s: %s\n%s",
                match_id, exc, traceback.format_exc()
            )
            match.status = MatchStatus.UPCOMING
            match.started_at = None
            if match.stream:
                match.stream.status = StreamStatus.IDLE
            await db.commit()

        return RedirectResponse(url=f"/admin/matches/{match_id}", status_code=303)


@router.post("/matches/{match_id}/stop")
async def match_stop(match_id: UUID):
    from app.services.match_runner import stop_match as runner_stop
    await runner_stop(str(match_id))

    async for db in _get_db():
        result = await db.execute(
            select(Match).where(Match.id == match_id).options(selectinload(Match.stream))
        )
        match = result.scalar_one_or_none()
        if match:
            match.status = MatchStatus.UPCOMING  # back to upcoming so it can be restarted
            match.started_at = None
            if match.stream:
                match.stream.status = StreamStatus.STOPPED
            await db.commit()

    return RedirectResponse(url=f"/admin/matches/{match_id}", status_code=303)


@router.post("/matches/{match_id}/cancel")
async def match_cancel(match_id: UUID):
    async for db in _get_db():
        result = await db.execute(
            select(Match).where(Match.id == match_id)
            .options(selectinload(Match.bets), selectinload(Match.stream))
        )
        match = result.scalar_one_or_none()
        if not match:
            return RedirectResponse(url="/admin/matches", status_code=303)

        # Stop runner if live
        from app.services.match_runner import stop_match as runner_stop
        await runner_stop(str(match_id))

        match.status = MatchStatus.CANCELLED
        if match.stream:
            match.stream.status = StreamStatus.STOPPED
        for bet in match.bets:
            if bet.status == BetStatus.ACTIVE:
                bet.status = BetStatus.CANCELLED
        await db.commit()

    return RedirectResponse(url="/admin/matches", status_code=303)


@router.post("/matches/{match_id}/settle")
async def match_settle(match_id: UUID, winner_id: UUID = Form(...)):
    """Manually settle a live match.

    ✅ FIX: Now calls settlement BEFORE stopping runner to preserve round data.
    Previously stopped runner first, which lost round counters.
    """
    async for db in _get_db():
        result = await db.execute(
            select(Match).where(Match.id == match_id)
        )
        match = result.scalar_one_or_none()
        if not match or match.status != MatchStatus.LIVE:
            return RedirectResponse(url=f"/admin/matches/{match_id}", status_code=303)

        # Determine winner player number
        winner_player = 1 if winner_id == match.fighter1_id else 2

    # ✅ FIX: Call settlement BEFORE stopping runner (settlement reads round data from it)
    from app.services.settlement import settle_match
    await settle_match(str(match_id), winner_player)

    # Stop runner AFTER settlement completes
    from app.services.match_runner import stop_match as runner_stop
    await runner_stop(str(match_id))

    return RedirectResponse(url=f"/admin/matches/{match_id}", status_code=303)


# ── Fighters ──

@router.get("/fighters", response_class=HTMLResponse)
async def fighters_list(request: Request):
    if r := _require_admin(request): return r
    async for db in _get_db():
        result = await db.execute(select(Fighter).order_by(Fighter.name))
        fighters = list(result.scalars().all())

        return templates.TemplateResponse("fighters.html", {
            "request": request,
            "active_page": "fighters",
            "fighters": fighters,
            "flash": None,
        })


@router.post("/fighters/new")
async def fighter_new(
    name: str = Form(...),
    slug: str = Form(...),
    character: str = Form(...),
    character_id: int = Form(...),
    llm_model: str = Form(...),
    agent_architecture: str = Form(""),
):
    async for db in _get_db():
        fighter = Fighter(
            name=name,
            slug=slug,
            character=character,
            character_id=character_id,
            llm_model=llm_model,
            agent_architecture=agent_architecture or None,
        )
        db.add(fighter)
        await db.commit()

    return RedirectResponse(url="/admin/fighters", status_code=303)
