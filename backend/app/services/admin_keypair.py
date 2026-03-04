"""
Admin keypair helper — loads and caches the Solana admin Keypair from settings.
"""

from __future__ import annotations

import base58
import logging

from solders.keypair import Keypair

from app.config import settings

logger = logging.getLogger(__name__)

_admin_keypair: Keypair | None = None


def get_admin_keypair() -> Keypair:
    """
    Load the admin keypair from ADMIN_KEYPAIR_B58 env var.

    The env var should contain the base58-encoded 64-byte secret key
    (as output by `solana-keygen new` and cat of the JSON array, then
    base58-encoded), OR the raw 64-byte JSON array format from Solana CLI
    (the array directly as a list of ints decoded from base58).

    For easy key generation:
        solana-keygen new --outfile /tmp/admin.json --no-bip39-passphrase
        python3 -c "
        import json, base58
        with open('/tmp/admin.json') as f: arr = json.load(f)
        print(base58.b58encode(bytes(arr)).decode())
        "
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
        key_bytes = base58.b58decode(raw)
        if len(key_bytes) == 64:
            _admin_keypair = Keypair.from_bytes(key_bytes)
        else:
            raise ValueError(f"Expected 64 bytes, got {len(key_bytes)}")
    except Exception as exc:
        raise ValueError(f"Invalid ADMIN_KEYPAIR_B58: {exc}") from exc

    logger.info("Admin keypair loaded: %s", str(_admin_keypair.pubkey()))
    return _admin_keypair


def get_rpc_url() -> str:
    """Return the appropriate Solana RPC URL based on settings."""
    from app.services.solana_tx import DEVNET_RPC, MAINNET_RPC
    return DEVNET_RPC if settings.use_devnet else MAINNET_RPC
