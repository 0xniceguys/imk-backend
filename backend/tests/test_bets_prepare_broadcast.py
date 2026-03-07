import base64

import pytest
from httpx import AsyncClient
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Bet, Fighter, Match


async def _mark_match_on_chain(db: AsyncSession, match_id: str, on_chain_match_pda: str) -> Match:
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one()
    match.on_chain_match_pda = on_chain_match_pda
    await db.commit()
    await db.refresh(match)
    return match


@pytest.mark.asyncio
async def test_prepare_bet_returns_unsigned_transaction(
    client: AsyncClient,
    db: AsyncSession,
    test_user,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    monkeypatch,
):
    user_kp = Keypair()
    test_user.wallet_address = str(user_kp.pubkey())
    await _mark_match_on_chain(db, match_with_stream.id, str(Keypair().pubkey()))
    f1, _ = fighters

    async def _fake_fetch_config(*_args, **_kwargs):
        return {
            "paused": False,
            "skr_mint": settings.skr_mint,
            "min_bet": 100,
            "max_bet": 400,
            "fee_bps": 500,
        }

    async def _fake_blockhash(*_args, **_kwargs):
        return str(Hash.default())

    monkeypatch.setattr("app.services.solana_tx.fetch_config", _fake_fetch_config)
    monkeypatch.setattr("app.services.solana_tx.get_recent_blockhash", _fake_blockhash)

    resp = await client.post(
        "/api/bets/prepare",
        json={
            "match_id": str(match_with_stream.id),
            "fighter_id": str(f1.id),
            "amount": 0.0002,
            "side": "A",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["transaction_base64"]

    tx = Transaction.from_bytes(base64.b64decode(payload["transaction_base64"]))
    assert len(tx.message.instructions) == 1
    ix = tx.message.instructions[0]
    program_id = str(tx.message.account_keys[ix.program_id_index])
    assert program_id == settings.betting_program_id


@pytest.mark.asyncio
async def test_broadcast_bet_persists_after_confirmed_tx(
    client: AsyncClient,
    db: AsyncSession,
    test_user,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    monkeypatch,
):
    user_kp = Keypair()
    test_user.wallet_address = str(user_kp.pubkey())
    await _mark_match_on_chain(db, match_with_stream.id, str(Keypair().pubkey()))
    f1, _ = fighters

    async def _fake_fetch_config(*_args, **_kwargs):
        return {
            "paused": False,
            "skr_mint": settings.skr_mint,
            "min_bet": 100,
            "max_bet": 400,
            "fee_bps": 500,
        }

    async def _fake_blockhash(*_args, **_kwargs):
        return str(Hash.default())

    async def _fake_broadcast_signed_transaction(**_kwargs):
        return "betsig-test-123"

    async def _fake_confirm(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.services.solana_tx.fetch_config", _fake_fetch_config)
    monkeypatch.setattr("app.services.solana_tx.get_recent_blockhash", _fake_blockhash)
    monkeypatch.setattr("app.api.bets._broadcast_signed_transaction", _fake_broadcast_signed_transaction)
    monkeypatch.setattr("app.services.solana_tx.confirm_transaction", _fake_confirm)

    prepare_resp = await client.post(
        "/api/bets/prepare",
        json={
            "match_id": str(match_with_stream.id),
            "fighter_id": str(f1.id),
            "amount": 0.0002,
            "side": "A",
        },
    )
    assert prepare_resp.status_code == 200, prepare_resp.text
    unsigned_tx_b64 = prepare_resp.json()["transaction_base64"]

    tx = Transaction.from_bytes(base64.b64decode(unsigned_tx_b64))
    tx.sign([user_kp], tx.message.recent_blockhash)
    signed_b64 = base64.b64encode(bytes(tx)).decode()

    broadcast_resp = await client.post(
        "/api/bets/broadcast",
        json={
            "match_id": str(match_with_stream.id),
            "signed_transaction_base64": signed_b64,
        },
    )
    assert broadcast_resp.status_code == 200, broadcast_resp.text
    payload = broadcast_resp.json()
    assert payload["tx_signature"] == "betsig-test-123"
    assert payload["on_chain_side"] == "A"
    assert payload["status"] == "active"

    result = await db.execute(select(Bet).where(Bet.tx_signature == "betsig-test-123"))
    bet = result.scalar_one_or_none()
    assert bet is not None
    assert float(bet.amount) == pytest.approx(0.0002, abs=1e-9)
