"""Public client runtime configuration for Flutter/Web apps.

This endpoint intentionally exposes only non-secret values needed by clients
to initialize auth/wallet UX and display contract metadata.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter

from app.config import settings
from app.services import solana_tx

logger = logging.getLogger(__name__)

router = APIRouter(tags=["client-config"])

_CACHE_TTL_SECONDS = 15.0
_cache_lock = asyncio.Lock()
_cached_contract: dict[str, Any] | None = None
_cached_at_monotonic = 0.0


def _cluster_name() -> str:
    return "devnet" if settings.use_devnet else "mainnet"


def _rpc_http() -> str:
    return solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC


def _rpc_ws(rpc_http: str) -> str:
    return rpc_http.replace("https://", "wss://").replace("http://", "ws://")


def _fallback_contract_config() -> dict[str, Any]:
    return {
        "fee_bps": int(settings.contract_fee_bps_default),
        "min_bet_base_units": int(settings.contract_min_bet_base_units_default),
        "max_bet_base_units": int(settings.contract_max_bet_base_units_default),
        "paused": False,
        "source": "fallback",
    }


def _base_units_to_ui_string(base_units: int, decimals: int) -> str:
    if decimals <= 0:
        return str(base_units)
    ui = Decimal(base_units) / (Decimal(10) ** decimals)
    ui_str = format(ui.normalize(), "f")
    if "." in ui_str:
        ui_str = ui_str.rstrip("0").rstrip(".")
    return ui_str or "0"


async def _load_contract_config() -> dict[str, Any]:
    global _cached_contract, _cached_at_monotonic

    now = time.monotonic()
    if _cached_contract and (now - _cached_at_monotonic) < _CACHE_TTL_SECONDS:
        return _cached_contract

    async with _cache_lock:
        now = time.monotonic()
        if _cached_contract and (now - _cached_at_monotonic) < _CACHE_TTL_SECONDS:
            return _cached_contract

        rpc = _rpc_http()
        try:
            cfg = await solana_tx.fetch_config(settings.betting_program_id, rpc)
            contract_cfg = {
                "fee_bps": int(cfg["fee_bps"]),
                "min_bet_base_units": int(cfg["min_bet"]),
                "max_bet_base_units": int(cfg["max_bet"]),
                "paused": bool(cfg["paused"]),
                "source": "on_chain",
            }
        except Exception as exc:
            logger.warning(
                "client-config: failed to fetch on-chain config, using fallback: %s",
                exc,
            )
            contract_cfg = _fallback_contract_config()

        _cached_contract = contract_cfg
        _cached_at_monotonic = time.monotonic()
        return contract_cfg


@router.get("/client-config")
async def get_client_config() -> dict[str, Any]:
    rpc_http = _rpc_http()
    contract_cfg = await _load_contract_config()
    now = datetime.now(timezone.utc).isoformat()

    return {
        "version": 1,
        "generated_at": now,
        "environment": _cluster_name(),
        "network": {
            "cluster": _cluster_name(),
            "rpc_http": rpc_http,
            "rpc_ws": _rpc_ws(rpc_http),
        },
        "privy": {
            "app_id": settings.privy_app_id,
            "client_id": settings.privy_client_id,
        },
        "contract": {
            "program_id": settings.betting_program_id,
            "skr_mint": settings.skr_mint,
            "fee_bps": contract_cfg["fee_bps"],
            "min_bet_base_units": contract_cfg["min_bet_base_units"],
            "max_bet_base_units": contract_cfg["max_bet_base_units"],
            "min_bet_ui": _base_units_to_ui_string(
                int(contract_cfg["min_bet_base_units"]), int(settings.token_decimals)
            ),
            "max_bet_ui": _base_units_to_ui_string(
                int(contract_cfg["max_bet_base_units"]), int(settings.token_decimals)
            ),
            "paused": contract_cfg["paused"],
            "source": contract_cfg["source"],
        },
        "token": {
            "symbol": settings.token_symbol,
            "decimals": int(settings.token_decimals),
        },
        "explorer": {
            "base_url": settings.explorer_base_url,
        },
        "features": {
            "server_side_signing": False,
            "client_signed_bets": True,
            "client_signed_claims": True,
            "dev_local_signer_bypass": bool(settings.dev_local_signer_bypass),
        },
    }
