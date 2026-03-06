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

from app.dependencies import get_db
from app.db.models import (
    Bet,
    BetStatus,
    Fighter,
    Match,
    MatchStatus,
    Stream,
    StreamStatus,
    Agent,
)
from app.services.emulator import M64P_ROOT
from app.services.actions import decode_controller_state
from app.services.match_runner import get_all_runners, get_runner

router = APIRouter(prefix="/admin", tags=["admin-views"])

_VALID_BUILTIN_AGENT_IDS = frozenset(
    {"random", "cpu", "lstm", "obj_belief", "disc_rssm", "transformer"}
)
_AGENT_STYLE_LABELS = {
    "disc_rssm": "RSSM",
    "transformer": "Transformer",
    "obj_belief": "Belief",
    "lstm": "LSTM",
    "cpu": "CPU",
    "random": "Random",
}

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


def _normalize_slug_for_savestate(slug: str) -> str:
    return slug.strip().lower().replace(" ", "").replace("-", "")


def _resolve_match_savestate(f1: Fighter, f2: Fighter) -> str | None:
    savestates = _discover_savestates()
    if not savestates:
        return None

    by_file = {}
    for s in savestates:
        name = Path(s["path"]).name.lower()
        by_file[name] = s["path"]

    s1 = _normalize_slug_for_savestate(f1.slug)
    s2 = _normalize_slug_for_savestate(f2.slug)
    preferred = [
        f"p1p2_{s1}_{s2}.st",  # exact seat mapping (P1 vs P2)
        "p1p2state.st",
        "kai_arcade_p1p2.st",
        f"p1p2_{s2}_{s1}.st",  # last-resort reverse pairing
    ]
    for filename in preferred:
        path = by_file.get(filename.lower())
        if path:
            return path
    return savestates[0]["path"]


def _resolve_fighter_agent_for_match(fighter: Fighter) -> tuple[str, str | None, str | None]:
    """Resolve runtime agent id + optional checkpoint + optional architecture."""
    if fighter.agent_id is not None and fighter.agent is not None:
        checkpoint = fighter.agent.checkpoint_path
        architecture = (fighter.agent.architecture or "").strip().lower() or None
        if checkpoint and Path(checkpoint).is_file():
            return f"custom_{fighter.agent.slug}", checkpoint, architecture
        if architecture in _VALID_BUILTIN_AGENT_IDS:
            return architecture, None, None

    arch = (fighter.agent_architecture or "").strip().lower()
    if arch in _VALID_BUILTIN_AGENT_IDS:
        return arch, None, None
    return "random", None, None


def _fighter_style_label(fighter: Fighter) -> str:
    if fighter.agent is not None and fighter.agent.architecture:
        return _AGENT_STYLE_LABELS.get(fighter.agent.architecture, fighter.agent.architecture)
    arch = (fighter.agent_architecture or "").strip().lower()
    if arch:
        return _AGENT_STYLE_LABELS.get(arch, arch)
    return "Unknown"


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
    async for db in _get_db():
        result = await db.execute(
            select(Fighter)
            .options(selectinload(Fighter.agent))
            .order_by(Fighter.name.asc())
        )
        fighters = list(result.scalars().all())

        fighter_rows = []
        for f in fighters:
            fighter_rows.append({
                "id": str(f.id),
                "name": f.name,
                "llm_model": f.llm_model,
                "style": _fighter_style_label(f),
                "slug": f.slug,
            })

        return templates.TemplateResponse("match_new.html", {
            "request": request,
            "active_page": "matches",
            "fighters": fighter_rows,
            "error": None,
            "selected_fighter1_id": None,
            "selected_fighter2_id": None,
            "label": "MK4-Classic",
            "best_of": 3,
        })


@router.post("/matches/new", response_class=HTMLResponse)
async def match_new_submit(
    request: Request,
    fighter1_id: UUID = Form(...),
    fighter2_id: UUID = Form(...),
    label: str = Form("MK4-Classic"),
    best_of: int = Form(3),
):
    async for db in _get_db():
        result = await db.execute(
            select(Fighter)
            .options(selectinload(Fighter.agent))
            .order_by(Fighter.name.asc())
        )
        fighters = list(result.scalars().all())
        fighter_rows = [{
            "id": str(f.id),
            "name": f.name,
            "llm_model": f.llm_model,
            "style": _fighter_style_label(f),
            "slug": f.slug,
        } for f in fighters]

        if fighter1_id == fighter2_id:
            return templates.TemplateResponse("match_new.html", {
                "request": request,
                "active_page": "matches",
                "fighters": fighter_rows,
                "error": "Fighter 1 and Fighter 2 must be different.",
                "selected_fighter1_id": str(fighter1_id),
                "selected_fighter2_id": str(fighter2_id),
                "label": label,
                "best_of": best_of,
            })

        fighter_map = {f.id: f for f in fighters}
        f1 = fighter_map.get(fighter1_id)
        f2 = fighter_map.get(fighter2_id)
        if f1 is None or f2 is None:
            return templates.TemplateResponse("match_new.html", {
                "request": request,
                "active_page": "matches",
                "fighters": fighter_rows,
                "error": "Selected fighter was not found. Please refresh and try again.",
                "selected_fighter1_id": str(fighter1_id),
                "selected_fighter2_id": str(fighter2_id),
                "label": label,
                "best_of": best_of,
            })

        savestate_path = _resolve_match_savestate(f1, f2)
        if not savestate_path:
            return templates.TemplateResponse("match_new.html", {
                "request": request,
                "active_page": "matches",
                "fighters": fighter_rows,
                "error": "No savestate files found on server. Add savestate files and retry.",
                "selected_fighter1_id": str(fighter1_id),
                "selected_fighter2_id": str(fighter2_id),
                "label": label,
                "best_of": best_of,
            })

        p1_agent, _, _ = _resolve_fighter_agent_for_match(f1)
        p2_agent, _, _ = _resolve_fighter_agent_for_match(f2)

        # ── CONTRACT FIRST: create on-chain, wait for confirmation ──
        from app.services.on_chain_match import create_match_on_chain
        try:
            on_chain_id, on_chain_pda = await create_match_on_chain(
                fighter1_name=f1.name,
                fighter2_name=f2.name,
            )
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).error(
                "On-chain create_match failed: %s", exc, exc_info=True,
            )
            return templates.TemplateResponse("match_new.html", {
                "request": request,
                "active_page": "matches",
                "fighters": fighter_rows,
                "error": f"On-chain match creation failed: {exc}",
                "selected_fighter1_id": str(fighter1_id),
                "selected_fighter2_id": str(fighter2_id),
                "label": label,
                "best_of": best_of,
            })

        # ── ONLY NOW: create DB row with PDA already set ──
        match = Match(
            fighter1_id=f1.id,
            fighter2_id=f2.id,
            p1_agent=p1_agent,
            p2_agent=p2_agent,
            scheduled_at=datetime.now(timezone.utc),
            label=label,
            savestate_path=savestate_path,
            best_of=best_of,
            on_chain_match_id=on_chain_id,
            on_chain_match_pda=on_chain_pda,
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

        # ── CONTRACT FIRST: lock match on-chain BEFORE going LIVE ──
        if match.on_chain_match_pda:
            try:
                from app.services.on_chain_match import lock_match_on_chain
                await lock_match_on_chain(match.on_chain_match_pda)
            except Exception as exc:
                import logging as _logging
                _logging.getLogger(__name__).error(
                    "lock_match_on_chain FAILED for %s: %s",
                    match_id, exc, exc_info=True,
                )
                return RedirectResponse(url=f"/admin/matches/{match_id}", status_code=303)

        # Resolve runtime agents from fighter policy.
        p1_agent_id = match.p1_agent
        p2_agent_id = match.p2_agent
        p1_checkpoint_path: str | None = None
        p2_checkpoint_path: str | None = None
        p1_architecture: str | None = None
        p2_architecture: str | None = None

        if match.fighter1:
            p1_agent_id, p1_checkpoint_path, p1_architecture = _resolve_fighter_agent_for_match(match.fighter1)
        if match.fighter2:
            p2_agent_id, p2_checkpoint_path, p2_architecture = _resolve_fighter_agent_for_match(match.fighter2)

        # ONLY NOW: set LIVE in DB (on-chain is already Locked)
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
    import logging as _logging
    _logger = _logging.getLogger(__name__)
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

        # ── CONTRACT FIRST: cancel on-chain BEFORE DB ──
        if match.on_chain_match_pda:
            try:
                from app.services.on_chain_match import cancel_match_on_chain
                await cancel_match_on_chain(match.on_chain_match_pda)
            except Exception as exc:
                _logger.error(
                    "On-chain cancel_match failed for %s: %s",
                    match_id, exc, exc_info=True,
                )
                # Contract is source of truth — abort if on-chain cancel fails
                return RedirectResponse(url=f"/admin/matches/{match_id}", status_code=303)

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
        from sqlalchemy.orm import selectinload as _sli
        result = await db.execute(
            select(Fighter).options(_sli(Fighter.agent)).order_by(Fighter.name)
        )
        fighters = list(result.scalars().all())

        # Serialize to plain dicts for tojson in the template
        fighters_data = [
            {
                "id": str(f.id),
                "name": f.name,
                "slug": f.slug,
                "character": f.character,
                "character_id": f.character_id,
                "llm_model": f.llm_model,
                "image_url": f.image_url,
                "agent_architecture": f.agent_architecture,
                "matches_played": f.matches_played,
                "matches_won": f.matches_won,
                "win_rate": round(f.matches_won / f.matches_played, 4) if f.matches_played else 0.0,
                "description": f.description,
                "origin": f.origin,
                "special_move": f.special_move,
                "fight_style": f.fight_style,
                "rank": f.rank,
            }
            for f in fighters
        ]

        agents_q = await db.execute(select(Agent).order_by(Agent.name))
        agents = list(agents_q.scalars().all())

        return templates.TemplateResponse("fighters.html", {
            "request": request,
            "active_page": "fighters",
            "fighters": fighters,
            "fighters_json": fighters_data,
            "agents": agents,
            "flash": None,
        })



@router.get("/fighters/{fighter_id}/json")
async def fighter_json(request: Request, fighter_id: UUID):
    """Return a single fighter as JSON — used by the JS edit panel."""
    _require_admin_api(request)
    async for db in _get_db():
        from sqlalchemy.orm import selectinload as _sli
        result = await db.execute(
            select(Fighter).where(Fighter.id == fighter_id).options(_sli(Fighter.agent))
        )
        f = result.scalar_one_or_none()
        if not f:
            raise HTTPException(404, "Fighter not found")
        return JSONResponse({
            "id": str(f.id),
            "name": f.name,
            "slug": f.slug,
            "character": f.character,
            "character_id": f.character_id,
            "llm_model": f.llm_model,
            "image_url": f.image_url,
            "agent_architecture": f.agent_architecture,
            "matches_played": f.matches_played,
            "matches_won": f.matches_won,
            "win_rate": round(f.matches_won / f.matches_played, 4) if f.matches_played else 0.0,
            "description": f.description,
            "origin": f.origin,
            "special_move": f.special_move,
            "fight_style": f.fight_style,
            "rank": f.rank,
            "agent_id": str(f.agent_id) if f.agent_id else None,
            "agent_name": f.agent.name if f.agent else None,
        })


@router.post("/fighters/new")
async def fighter_new(request: Request):
    """Create a new fighter from multipart form (supports image upload)."""
    _require_admin_api(request)
    import shutil
    from pathlib import Path
    form = await request.form()

    name = str(form.get("name", "")).strip()
    slug = str(form.get("slug", "")).strip()
    character = str(form.get("character", "")).strip()
    character_id = int(form.get("character_id", 0) or 0)
    llm_model = str(form.get("llm_model", "")).strip()
    agent_architecture = str(form.get("agent_architecture", "")).strip() or None
    description = str(form.get("description", "")).strip() or None
    origin = str(form.get("origin", "")).strip() or None
    special_move = str(form.get("special_move", "")).strip() or None
    fight_style = str(form.get("fight_style", "")).strip() or None
    rank_raw = form.get("rank", "")
    rank = int(rank_raw) if rank_raw and str(rank_raw).strip().isdigit() else None

    if not all([name, slug, character, llm_model]):
        raise HTTPException(400, "name, slug, character, llm_model are required")

    async for db in _get_db():
        fighter = Fighter(
            name=name, slug=slug, character=character, character_id=character_id,
            llm_model=llm_model, agent_architecture=agent_architecture,
            description=description, origin=origin, special_move=special_move,
            fight_style=fight_style, rank=rank,
        )
        db.add(fighter)
        await db.commit()
        await db.refresh(fighter)

        # Handle optional image upload
        image_file = form.get("image")
        if image_file and hasattr(image_file, "filename") and image_file.filename:
            IMAGE_DIR = Path(__file__).resolve().parent.parent / "uploads" / "fighters"
            IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            ext = Path(image_file.filename).suffix.lower() or ".jpg"
            filename = f"{fighter.slug}{ext}"
            file_path = IMAGE_DIR / filename
            with file_path.open("wb") as f:
                shutil.copyfileobj(image_file.file, f)
            fighter.image_url = f"/uploads/fighters/{filename}"
            await db.commit()

        return JSONResponse({"id": str(fighter.id), "name": fighter.name})


@router.post("/fighters/{fighter_id}/edit")
async def fighter_edit(request: Request, fighter_id: UUID):
    """Edit a fighter — accepts JSON body with any updatable fields."""
    _require_admin_api(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Request body must be valid JSON")
    if not data:
        raise HTTPException(400, "No fields provided to update")

    async for db in _get_db():
        from sqlalchemy.orm import selectinload as _sli
        result = await db.execute(
            select(Fighter).where(Fighter.id == fighter_id).options(_sli(Fighter.agent))
        )
        f = result.scalar_one_or_none()
        if not f:
            raise HTTPException(404, "Fighter not found")

        # Text / enum fields
        for field in ("name", "llm_model", "agent_architecture", "description",
                      "origin", "special_move", "fight_style"):
            if field in data and data[field] is not None:
                setattr(f, field, data[field] or None if field not in ("name", "llm_model") else data[field])

        # Character name
        if "character" in data and data["character"]:
            f.character = data["character"]
        # Numeric fields
        if "rank" in data:
            f.rank = int(data["rank"]) if data["rank"] not in (None, "", "null") else None

        # character_id — critical game engine field
        if "character_id" in data and data["character_id"] is not None:
            try:
                f.character_id = int(data["character_id"])
            except (ValueError, TypeError):
                pass

        # Stat corrections — only update if explicitly provided
        if "matches_played" in data and data["matches_played"] is not None:
            try:
                f.matches_played = max(0, int(data["matches_played"]))
            except (ValueError, TypeError):
                pass
        if "matches_won" in data and data["matches_won"] is not None:
            try:
                mp = f.matches_played or 0
                f.matches_won = max(0, min(int(data["matches_won"]), mp))
            except (ValueError, TypeError):
                pass

        # Agent assignment
        if "agent_id" in data:
            if data["agent_id"]:
                try:
                    agent_uuid = UUID(data["agent_id"])
                    ar = await db.execute(select(Agent).where(Agent.id == agent_uuid))
                    agent = ar.scalar_one_or_none()
                    if agent:
                        f.agent_id = agent_uuid
                    else:
                        raise HTTPException(400, f"Agent {data['agent_id']} not found")
                except ValueError:
                    raise HTTPException(400, "Invalid agent_id format")
            else:
                f.agent_id = None  # unlink agent

        await db.commit()
        return JSONResponse({"ok": True})


@router.post("/fighters/{fighter_id}/image")
async def fighter_image_upload(request: Request, fighter_id: UUID):
    """Upload / replace fighter image from admin panel."""
    _require_admin_api(request)
    import shutil
    from pathlib import Path

    form = await request.form()
    image_file = form.get("image")
    if not image_file or not hasattr(image_file, "filename") or not image_file.filename:
        raise HTTPException(400, "No image file provided")

    async for db in _get_db():
        result = await db.execute(select(Fighter).where(Fighter.id == fighter_id))
        f = result.scalar_one_or_none()
        if not f:
            raise HTTPException(404, "Fighter not found")

        IMAGE_DIR = Path(__file__).resolve().parent.parent / "uploads" / "fighters"
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(image_file.filename).suffix.lower() or ".jpg"
        filename = f"{f.slug}{ext}"
        file_path = IMAGE_DIR / filename
        with file_path.open("wb") as fp:
            shutil.copyfileobj(image_file.file, fp)

        f.image_url = f"/uploads/fighters/{filename}"
        await db.commit()

        return JSONResponse({"ok": True, "image_url": f.image_url})


@router.post("/fighters/{fighter_id}/delete")
async def fighter_delete(request: Request, fighter_id: UUID):
    """Delete a fighter — NULLs match references first to avoid FK violations."""
    _require_admin_api(request)
    from sqlalchemy import update as sql_update
    async for db in _get_db():
        result = await db.execute(select(Fighter).where(Fighter.id == fighter_id))
        f = result.scalar_one_or_none()
        if not f:
            raise HTTPException(404, "Fighter not found")

        # NULL out all match references before deleting
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
        await db.commit()

        await db.delete(f)
        await db.commit()
        return JSONResponse({"ok": True})



# ── Agents ──

@router.get("/agents", response_class=HTMLResponse)
async def agents_list(request: Request):
    if r := _require_admin(request): return r
    async for db in _get_db():
        agents_q = await db.execute(select(Agent).order_by(Agent.created_at.desc()))
        agents = list(agents_q.scalars().all())
        fighters_q = await db.execute(select(Fighter).order_by(Fighter.name))
        fighters = list(fighters_q.scalars().all())
        return templates.TemplateResponse("agents.html", {
            "request": request, "active_page": "agents",
            "agents": agents, "fighters": fighters,
        })

@router.get("/agents/{agent_id}/json")
async def agent_json(request: Request, agent_id: UUID):
    _require_admin_api(request)
    async for db in _get_db():
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        a = result.scalar_one_or_none()
        if not a: raise HTTPException(404, "Agent not found")
        fq = await db.execute(select(Fighter).where(Fighter.agent_id == agent_id))
        assigned = fq.scalar_one_or_none()
        return JSONResponse({"id": str(a.id), "name": a.name, "slug": a.slug,
            "architecture": a.architecture, "description": a.description,
            "checkpoint_path": a.checkpoint_path, "file_size_bytes": a.file_size_bytes,
            "is_public": a.is_public, "created_at": a.created_at.isoformat(),
            "assigned_fighter_id": str(assigned.id) if assigned else None,
            "assigned_fighter_name": assigned.name if assigned else None,
        })

@router.post("/agents/{agent_id}/edit")
async def agent_edit(request: Request, agent_id: UUID):
    _require_admin_api(request)
    data = await request.json()
    async for db in _get_db():
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        a = result.scalar_one_or_none()
        if not a: raise HTTPException(404, "Agent not found")
        if data.get("name"): a.name = data["name"]
        if "description" in data: a.description = data["description"] or None
        if data.get("architecture"): a.architecture = data["architecture"]
        if "is_public" in data: a.is_public = bool(data["is_public"])
        await db.commit()
        fq = await db.execute(select(Fighter).where(Fighter.agent_id == agent_id))
        for f in fq.scalars().all(): f.agent_id = None
        new_fid = data.get("assigned_fighter_id")
        if new_fid:
            try:
                fr = await db.execute(select(Fighter).where(Fighter.id == UUID(new_fid)))
                ff = fr.scalar_one_or_none()
                if ff: ff.agent_id = agent_id
            except Exception: pass
        await db.commit()
        return JSONResponse({"ok": True})

@router.post("/agents/new")
async def agent_new(request: Request):
    _require_admin_api(request)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    slug = str(form.get("slug", "")).strip()
    architecture = str(form.get("architecture", "lstm")).strip()
    description = str(form.get("description", "")).strip() or None
    is_public = str(form.get("is_public", "true")).lower() == "true"
    fighter_id_str = str(form.get("fighter_id", "")).strip()
    agent_file = form.get("agent_file")
    if not name or not slug: raise HTTPException(400, "name and slug required")
    if not agent_file or not hasattr(agent_file, "filename") or not agent_file.filename:
        raise HTTPException(400, "ONNX file required")
    AGENT_DIR = Path(__file__).resolve().parent.parent / "uploads" / "agents"
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    file_path = AGENT_DIR / f"{slug}.onnx"
    content = await agent_file.read()
    with file_path.open("wb") as fp: fp.write(content)
    async for db in _get_db():
        agent = Agent(name=name, slug=slug, architecture=architecture, description=description,
            checkpoint_path=str(file_path), file_size_bytes=len(content), is_public=is_public)
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        if fighter_id_str:
            try:
                fr = await db.execute(select(Fighter).where(Fighter.id == UUID(fighter_id_str)))
                ff = fr.scalar_one_or_none()
                if ff: ff.agent_id = agent.id; await db.commit()
            except Exception: pass
        return JSONResponse({"id": str(agent.id), "name": agent.name})

@router.post("/agents/{agent_id}/delete")
async def agent_delete(request: Request, agent_id: UUID):
    _require_admin_api(request)
    async for db in _get_db():
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        a = result.scalar_one_or_none()
        if not a: raise HTTPException(404, "Agent not found")
        fq = await db.execute(select(Fighter).where(Fighter.agent_id == agent_id))
        for f in fq.scalars().all(): f.agent_id = None
        await db.commit()
        await db.delete(a)
        await db.commit()
        return JSONResponse({"ok": True})


# ── Agents ──

@router.get("/agents", response_class=HTMLResponse)
async def agents_list(request: Request):
    if r := _require_admin(request): return r
    async for db in _get_db():
        agents_q = await db.execute(select(Agent).order_by(Agent.created_at.desc()))
        agents = list(agents_q.scalars().all())
        fighters_q = await db.execute(select(Fighter).order_by(Fighter.name))
        fighters = list(fighters_q.scalars().all())
        return templates.TemplateResponse("agents.html", {
            "request": request, "active_page": "agents",
            "agents": agents, "fighters": fighters,
        })


@router.get("/agents/{agent_id}/json")
async def agent_json(request: Request, agent_id: UUID):
    _require_admin_api(request)
    async for db in _get_db():
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        a = result.scalar_one_or_none()
        if not a: raise HTTPException(404, "Agent not found")
        fq = await db.execute(select(Fighter).where(Fighter.agent_id == agent_id))
        assigned = fq.scalar_one_or_none()
        return JSONResponse({"id": str(a.id), "name": a.name, "slug": a.slug,
            "architecture": a.architecture, "description": a.description,
            "checkpoint_path": a.checkpoint_path, "file_size_bytes": a.file_size_bytes,
            "is_public": a.is_public, "created_at": a.created_at.isoformat(),
            "assigned_fighter_id": str(assigned.id) if assigned else None,
            "assigned_fighter_name": assigned.name if assigned else None,
        })


@router.post("/agents/{agent_id}/edit")
async def agent_edit(request: Request, agent_id: UUID):
    _require_admin_api(request)
    data = await request.json()
    async for db in _get_db():
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        a = result.scalar_one_or_none()
        if not a: raise HTTPException(404, "Agent not found")
        if data.get("name"): a.name = data["name"]
        if "description" in data: a.description = data["description"] or None
        if data.get("architecture"): a.architecture = data["architecture"]
        if "is_public" in data: a.is_public = bool(data["is_public"])
        await db.commit()
        fq = await db.execute(select(Fighter).where(Fighter.agent_id == agent_id))
        for f in fq.scalars().all(): f.agent_id = None
        new_fid = data.get("assigned_fighter_id")
        if new_fid:
            try:
                from uuid import UUID as _UUID
                fr = await db.execute(select(Fighter).where(Fighter.id == _UUID(new_fid)))
                ff = fr.scalar_one_or_none()
                if ff: ff.agent_id = agent_id
            except Exception: pass
        await db.commit()
        return JSONResponse({"ok": True})


@router.post("/agents/new")
async def agent_new(request: Request):
    _require_admin_api(request)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    slug = str(form.get("slug", "")).strip()
    architecture = str(form.get("architecture", "lstm")).strip()
    description = str(form.get("description", "")).strip() or None
    is_public = str(form.get("is_public", "true")).lower() == "true"
    fighter_id_str = str(form.get("fighter_id", "")).strip()
    agent_file = form.get("agent_file")
    if not name or not slug: raise HTTPException(400, "name and slug required")
    if not agent_file or not hasattr(agent_file, "filename") or not agent_file.filename:
        raise HTTPException(400, "ONNX file required")
    AGENT_DIR = Path(__file__).resolve().parent.parent / "uploads" / "agents"
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    file_path = AGENT_DIR / f"{slug}.onnx"
    content = await agent_file.read()
    with file_path.open("wb") as fp: fp.write(content)
    async for db in _get_db():
        agent = Agent(name=name, slug=slug, architecture=architecture, description=description,
            checkpoint_path=str(file_path), file_size_bytes=len(content), is_public=is_public)
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        if fighter_id_str:
            try:
                from uuid import UUID as _UUID
                fr = await db.execute(select(Fighter).where(Fighter.id == _UUID(fighter_id_str)))
                ff = fr.scalar_one_or_none()
                if ff: ff.agent_id = agent.id; await db.commit()
            except Exception: pass
        return JSONResponse({"id": str(agent.id), "name": agent.name})


@router.post("/agents/{agent_id}/delete")
async def agent_delete(request: Request, agent_id: UUID):
    _require_admin_api(request)
    async for db in _get_db():
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        a = result.scalar_one_or_none()
        if not a: raise HTTPException(404, "Agent not found")
        fq = await db.execute(select(Fighter).where(Fighter.agent_id == agent_id))
        for f in fq.scalars().all(): f.agent_id = None
        await db.commit()
        await db.delete(a)
        await db.commit()
        return JSONResponse({"ok": True})
