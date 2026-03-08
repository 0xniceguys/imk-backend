import base64

import pytest
from httpx import AsyncClient
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction


@pytest.mark.asyncio
async def test_prepare_withdraw_creates_recipient_ata_when_missing(
    client: AsyncClient,
    test_user,
    monkeypatch,
):
    user_wallet = Keypair()
    recipient_wallet = Keypair()
    src_ata = Keypair()

    test_user.wallet_address = str(user_wallet.pubkey())

    async def _fake_blockhash(_rpc_url: str) -> str:
        return str(Hash.default())

    async def _fake_get_token_account(owner: str, _mint: str, _rpc_url: str):
        if owner == str(user_wallet.pubkey()):
            return str(src_ata.pubkey())
        if owner == str(recipient_wallet.pubkey()):
            return None
        return None

    monkeypatch.setattr("app.api.wallet.get_recent_blockhash", _fake_blockhash)
    monkeypatch.setattr("app.api.wallet.get_token_account", _fake_get_token_account)

    resp = await client.post(
        "/api/wallet/withdraw/prepare",
        json={
            "token": "seeker",
            "to_address": str(recipient_wallet.pubkey()),
            "amount": 1.0,
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "transaction_base64" in payload
    assert "will be created" in payload["message"]

    tx = Transaction.from_bytes(base64.b64decode(payload["transaction_base64"]))
    assert len(tx.message.instructions) == 2

    first_ix = tx.message.instructions[0]
    second_ix = tx.message.instructions[1]
    first_program = str(tx.message.account_keys[first_ix.program_id_index])
    second_program = str(tx.message.account_keys[second_ix.program_id_index])

    assert first_program == "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
    assert second_program == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
