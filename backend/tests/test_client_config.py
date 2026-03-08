"""Tests for public /api/client-config endpoint."""

import pytest
from httpx import AsyncClient

from app.api import client_config as client_config_module


def _reset_client_config_cache() -> None:
    client_config_module._cached_contract = None
    client_config_module._cached_at_monotonic = 0.0


@pytest.mark.asyncio
async def test_client_config_returns_on_chain_values(
    unauthed_client: AsyncClient,
    monkeypatch,
):
    _reset_client_config_cache()

    async def _fake_fetch_config(program_id: str, rpc_url: str):
        return {
            "fee_bps": 321,
            "min_bet": 111,
            "max_bet": 999,
            "paused": False,
        }

    monkeypatch.setattr(client_config_module.solana_tx, "fetch_config", _fake_fetch_config)

    resp = await unauthed_client.get("/api/client-config")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["environment"] in ("devnet", "mainnet")
    assert data["network"]["cluster"] == data["environment"]
    assert data["privy"]["app_id"]
    assert data["privy"]["client_id"]
    assert data["contract"]["fee_bps"] == 321
    assert data["contract"]["min_bet_base_units"] == 111
    assert data["contract"]["max_bet_base_units"] == 999
    assert data["contract"]["source"] == "on_chain"
    assert data["token"]["symbol"] == "SKR"
    assert data["explorer"]["base_url"] == "https://solscan.io"


@pytest.mark.asyncio
async def test_client_config_falls_back_when_on_chain_fetch_fails(
    unauthed_client: AsyncClient,
    monkeypatch,
):
    _reset_client_config_cache()

    async def _failing_fetch_config(program_id: str, rpc_url: str):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(client_config_module.solana_tx, "fetch_config", _failing_fetch_config)

    resp = await unauthed_client.get("/api/client-config")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["contract"]["source"] == "fallback"
    assert data["contract"]["fee_bps"] == 500
    assert data["contract"]["min_bet_base_units"] == 100
    assert data["contract"]["max_bet_base_units"] == 400
