from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bet, BetStatus, Fighter, Match, MatchStatus, User


async def _create_won_onchain_bet(
    db: AsyncSession,
    user: User,
    fighters: tuple[Fighter, Fighter],
    on_chain_match_pda: str,
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
async def test_prepare_claim_returns_unsigned_transaction(
    client: AsyncClient,
    db: AsyncSession,
    fighters: tuple[Fighter, Fighter],
    test_user: User,
    monkeypatch,
):
    user_kp = Keypair()
    test_user.wallet_address = str(user_kp.pubkey())
    bet = await _create_won_onchain_bet(db, test_user, fighters, str(Keypair().pubkey()))

    treasury_kp = Keypair()
    admin_kp = Keypair()

    async def _fake_fetch_match(*_args, **_kwargs):
        return {"status": 2, "winner": 1}

    async def _fake_fetch_config(*_args, **_kwargs):
        return {"treasury_wallet": str(treasury_kp.pubkey()), "admin": str(admin_kp.pubkey())}

    async def _fake_blockhash(*_args, **_kwargs):
        return str(Hash.default())

    monkeypatch.setattr("app.services.solana_tx.fetch_match", _fake_fetch_match)
    monkeypatch.setattr("app.services.solana_tx.fetch_config", _fake_fetch_config)
    monkeypatch.setattr("app.services.solana_tx.get_recent_blockhash", _fake_blockhash)
    monkeypatch.setattr("app.services.admin_keypair.get_admin_keypair", lambda: admin_kp)

    resp = await client.post(f"/api/bets/{bet.id}/claim/prepare")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["transaction_base64"]


@pytest.mark.asyncio
async def test_broadcast_claim_marks_bet_claimed(
    client: AsyncClient,
    db: AsyncSession,
    fighters: tuple[Fighter, Fighter],
    test_user: User,
    monkeypatch,
):
    user_kp = Keypair()
    test_user.wallet_address = str(user_kp.pubkey())
    bet = await _create_won_onchain_bet(db, test_user, fighters, str(Keypair().pubkey()))

    treasury_kp = Keypair()
    admin_kp = Keypair()

    async def _fake_fetch_match(*_args, **_kwargs):
        return {"status": 2, "winner": 1}

    async def _fake_fetch_config(*_args, **_kwargs):
        return {"treasury_wallet": str(treasury_kp.pubkey()), "admin": str(admin_kp.pubkey())}

    async def _fake_blockhash(*_args, **_kwargs):
        return str(Hash.default())

    async def _fake_broadcast(**_kwargs):
        return "claimsig-test-123"

    async def _fake_confirm(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.services.solana_tx.fetch_match", _fake_fetch_match)
    monkeypatch.setattr("app.services.solana_tx.fetch_config", _fake_fetch_config)
    monkeypatch.setattr("app.services.solana_tx.get_recent_blockhash", _fake_blockhash)
    monkeypatch.setattr("app.services.admin_keypair.get_admin_keypair", lambda: admin_kp)
    monkeypatch.setattr("app.api.claim._broadcast_signed_transaction", _fake_broadcast)
    monkeypatch.setattr("app.services.solana_tx.confirm_transaction", _fake_confirm)

    prepare_resp = await client.post(f"/api/bets/{bet.id}/claim/prepare")
    assert prepare_resp.status_code == 200, prepare_resp.text

    tx = Transaction.from_bytes(base64.b64decode(prepare_resp.json()["transaction_base64"]))
    tx.sign([user_kp], tx.message.recent_blockhash)
    signed_b64 = base64.b64encode(bytes(tx)).decode()

    broadcast_resp = await client.post(
        f"/api/bets/{bet.id}/claim/broadcast",
        json={"signed_transaction_base64": signed_b64},
    )
    assert broadcast_resp.status_code == 200, broadcast_resp.text
    payload = broadcast_resp.json()
    assert payload["status"] == "claimed"
    assert payload["tx_signature"] == "claimsig-test-123"

    bet_id = bet.id
    db.expire_all()
    result = await db.execute(select(Bet).where(Bet.id == bet_id))
    updated = result.scalar_one()
    assert updated.status == BetStatus.CLAIMED
    assert updated.claim_tx_signature == "claimsig-test-123"
