from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from solders.hash import Hash
from solders.keypair import Keypair
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Bet, BetStatus, Fighter, Match, MatchStatus, User


async def _create_won_onchain_bet(
    db: AsyncSession,
    user: User,
    fighters: tuple[Fighter, Fighter],
    on_chain_match_pda: str = "11111111111111111111111111111111",
) -> Bet:
    f1, f2 = fighters
    match = Match(
        fighter1_id=f1.id,
        fighter2_id=f2.id,
        status=MatchStatus.COMPLETED,
        scheduled_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        winner_id=f1.id,
        on_chain_match_pda=on_chain_match_pda,
    )
    db.add(match)
    await db.flush()

    bet = Bet(
        user_id=user.id,
        match_id=match.id,
        fighter_id=f1.id,
        amount=1.0,
        odds_at_placement=1.5,
        status=BetStatus.WON,
        on_chain_side="A",
    )
    db.add(bet)
    await db.commit()
    await db.refresh(bet)
    return bet


@pytest.mark.asyncio
async def test_claim_invalid_uuid_rejected(client: AsyncClient):
    resp = await client.post("/api/bets/not-a-uuid/claim", json={"privy_jwt": "dummy.jwt"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_claim_rejects_onchain_winner_mismatch(
    client: AsyncClient,
    db: AsyncSession,
    fighters: tuple[Fighter, Fighter],
    test_user: User,
    monkeypatch,
):
    bet = await _create_won_onchain_bet(db, test_user, fighters)

    async def fake_fetch_match(*_args, **_kwargs):
        return {"status": 2, "winner": 2}  # on-chain winner side B

    async def fake_fetch_config(*_args, **_kwargs):
        kp = Keypair()
        return {"treasury_wallet": str(kp.pubkey()), "admin": str(kp.pubkey()), "fee_bps": 500}

    monkeypatch.setattr("app.services.solana_tx.fetch_match", fake_fetch_match)
    monkeypatch.setattr("app.services.solana_tx.fetch_config", fake_fetch_config)

    bet_id = bet.id
    resp = await client.post(f"/api/bets/{bet_id}/claim", json={"privy_jwt": "dummy.jwt"})
    assert resp.status_code == 409
    assert "winner" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_claim_confirms_before_marking_claimed(
    client: AsyncClient,
    db: AsyncSession,
    fighters: tuple[Fighter, Fighter],
    test_user: User,
    monkeypatch,
):
    bet = await _create_won_onchain_bet(db, test_user, fighters)

    async def fake_fetch_match(*_args, **_kwargs):
        return {"status": 2, "winner": 1}

    async def fake_fetch_config(*_args, **_kwargs):
        kp = Keypair()
        return {"treasury_wallet": str(kp.pubkey()), "admin": str(kp.pubkey()), "fee_bps": 500}

    async def fake_blockhash(*_args, **_kwargs):
        return str(Hash.default())

    def fake_build_claim_ix(*_args, **_kwargs):
        return b"unsigned_tx"

    async def fake_sign(*_args, **_kwargs):
        return "claimsig111", None

    async def fake_confirm(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.services.solana_tx.fetch_match", fake_fetch_match)
    monkeypatch.setattr("app.services.solana_tx.fetch_config", fake_fetch_config)
    monkeypatch.setattr("app.services.solana_tx.get_recent_blockhash", fake_blockhash)
    monkeypatch.setattr("app.services.solana_tx.build_claim_ix", fake_build_claim_ix)
    monkeypatch.setattr("app.services.solana_tx.confirm_transaction", fake_confirm)
    monkeypatch.setattr("app.services.privy_wallet.get_wallet_id_and_sign", fake_sign)
    monkeypatch.setattr("app.services.admin_keypair.get_admin_keypair", lambda: Keypair())

    bet_id = bet.id
    resp = await client.post(f"/api/bets/{bet_id}/claim", json={"privy_jwt": "dummy.jwt"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "claimed"

    db.expire_all()
    result = await db.execute(select(Bet).where(Bet.id == bet_id))
    updated = result.scalar_one()
    assert updated.status == BetStatus.CLAIMED
    assert updated.claim_tx_signature == "claimsig111"


@pytest.mark.asyncio
async def test_claim_not_confirmed_does_not_flip_db_status(
    client: AsyncClient,
    db: AsyncSession,
    fighters: tuple[Fighter, Fighter],
    test_user: User,
    monkeypatch,
):
    bet = await _create_won_onchain_bet(db, test_user, fighters)

    async def fake_fetch_match(*_args, **_kwargs):
        return {"status": 2, "winner": 1}

    async def fake_fetch_config(*_args, **_kwargs):
        kp = Keypair()
        return {"treasury_wallet": str(kp.pubkey()), "admin": str(kp.pubkey()), "fee_bps": 500}

    async def fake_blockhash(*_args, **_kwargs):
        return str(Hash.default())

    def fake_build_claim_ix(*_args, **_kwargs):
        return b"unsigned_tx"

    async def fake_sign(*_args, **_kwargs):
        return "claimsig222", None

    async def fake_confirm(*_args, **_kwargs):
        return False

    monkeypatch.setattr("app.services.solana_tx.fetch_match", fake_fetch_match)
    monkeypatch.setattr("app.services.solana_tx.fetch_config", fake_fetch_config)
    monkeypatch.setattr("app.services.solana_tx.get_recent_blockhash", fake_blockhash)
    monkeypatch.setattr("app.services.solana_tx.build_claim_ix", fake_build_claim_ix)
    monkeypatch.setattr("app.services.solana_tx.confirm_transaction", fake_confirm)
    monkeypatch.setattr("app.services.privy_wallet.get_wallet_id_and_sign", fake_sign)
    monkeypatch.setattr("app.services.admin_keypair.get_admin_keypair", lambda: Keypair())

    resp = await client.post(f"/api/bets/{bet.id}/claim", json={"privy_jwt": "dummy.jwt"})
    assert resp.status_code == 502
    assert "not confirmed" in resp.json()["detail"].lower()

    result = await db.execute(select(Bet).where(Bet.id == bet.id))
    updated = result.scalar_one()
    assert updated.status == BetStatus.WON
    assert updated.claim_tx_signature is None


@pytest.mark.asyncio
async def test_settlement_onchain_failure_does_not_mark_db_completed(
    db: AsyncSession,
    fighters: tuple[Fighter, Fighter],
    test_user: User,
    monkeypatch,
):
    from app.services.settlement import OnChainSettlementError, settle_match

    f1, f2 = fighters
    match = Match(
        fighter1_id=f1.id,
        fighter2_id=f2.id,
        status=MatchStatus.LIVE,
        scheduled_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        on_chain_match_pda="11111111111111111111111111111111",
    )
    db.add(match)
    await db.flush()
    bet = Bet(
        user_id=test_user.id,
        match_id=match.id,
        fighter_id=f1.id,
        amount=2.0,
        odds_at_placement=1.0,
        status=BetStatus.ACTIVE,
        on_chain_side="A",
    )
    db.add(bet)
    await db.commit()

    # settlement.py uses app.db.engine.async_session directly; point it at this test DB.
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.db.engine.async_session", session_factory)

    async def fake_fetch_config(*_args, **_kwargs):
        kp = Keypair()
        return {"fee_bps": 500, "treasury_wallet": str(kp.pubkey()), "admin": str(kp.pubkey())}

    async def fake_resolve(*_args, **_kwargs):
        raise OnChainSettlementError("forced on-chain failure")

    monkeypatch.setattr("app.services.solana_tx.fetch_config", fake_fetch_config)
    monkeypatch.setattr("app.services.settlement._resolve_on_chain", fake_resolve)

    with pytest.raises(OnChainSettlementError):
        await settle_match(str(match.id), winner_player=1)

    result = await db.execute(select(Match).where(Match.id == match.id))
    after_match = result.scalar_one()
    assert after_match.status == MatchStatus.LIVE
    assert after_match.winner_id is None

    result = await db.execute(select(Bet).where(Bet.id == bet.id))
    after_bet = result.scalar_one()
    assert after_bet.status == BetStatus.ACTIVE
