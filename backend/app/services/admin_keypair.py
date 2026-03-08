"""
Admin keypair helper — loads and caches the Solana admin Keypair from settings.
"""

from __future__ import annotations

import logging

from solders.keypair import Keypair

from app.config import settings

logger = logging.getLogger(__name__)

_admin_keypair: Keypair | None = None


def get_admin_keypair() -> Keypair:
    """
    Load the admin keypair from ADMIN_KEYPAIR_B58 env var.

    The env var should contain the base58-encoded 64-byte secret key.
    """
    global _admin_keypair
    if _admin_keypair is not None:
        return _admin_keypair

    raw = settings.admin_keypair_b58.strip()
    if not raw:
        raise ValueError(
            "ADMIN_KEYPAIR_B58 is not set. "
            "Add it to .env before calling admin on-chain instructions."
        )

    try:
        _admin_keypair = Keypair.from_base58_string(raw)
    except Exception as exc:
        raise ValueError(f"Invalid ADMIN_KEYPAIR_B58: {exc}") from exc

    logger.info("Admin keypair loaded: %s", str(_admin_keypair.pubkey()))
    return _admin_keypair


def get_rpc_url() -> str:
    """Return the appropriate Solana RPC URL based on settings."""
    from app.services.solana_tx import DEVNET_RPC, MAINNET_RPC
    return DEVNET_RPC if settings.use_devnet else MAINNET_RPC
