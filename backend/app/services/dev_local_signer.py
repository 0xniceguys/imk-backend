"""
Dev-only local signer for backend integration tests.

This bypass is intended for localhost testing only. It signs unsigned
transactions with local keypair JSON files instead of Privy.
"""

from __future__ import annotations

import json
from pathlib import Path

from solders.keypair import Keypair
from solders.transaction import Transaction

from app.config import settings
from app.db.models import User
from app.services import solana_tx

_ALIAS_USER1 = {
    "user1",
    "bettor1",
    "dev-user1",
    "dev-bettor-1",
    "dev-test-user",
}
_ALIAS_USER2 = {
    "user2",
    "bettor2",
    "dev-user2",
    "dev-bettor-2",
    "dev-test-user-2",
}

_KEYPAIR_CACHE: dict[str, Keypair] = {}


def enabled() -> bool:
    return bool(settings.dev_local_signer_bypass)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _keys_dir() -> Path:
    p = Path(settings.dev_local_signer_keys_dir)
    if not p.is_absolute():
        p = _backend_root() / p
    return p


def _alias_to_path(alias: str) -> Path:
    if alias == "user1":
        filename = settings.dev_local_signer_user1_keyfile
    elif alias == "user2":
        filename = settings.dev_local_signer_user2_keyfile
    else:
        raise ValueError(f"Unknown dev signer alias: {alias}")

    path = Path(filename)
    if not path.is_absolute():
        path = _keys_dir() / path
    return path


def normalize_dev_subject(subject: str | None) -> str:
    token = (subject or "").strip().lower()
    if not token:
        return "user1"
    if token in _ALIAS_USER1:
        return "user1"
    if token in _ALIAS_USER2:
        return "user2"
    raise ValueError(
        f"Unknown dev user subject '{subject}'. "
        "Use one of: user1, user2, bettor1, bettor2."
    )


def _load_keypair(path: Path) -> Keypair:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list) or len(raw) != 64:
        raise ValueError(f"Keypair file must be a JSON array[64]: {path}")
    key_bytes = bytes(int(v) for v in raw)
    return Keypair.from_bytes(key_bytes)


def get_keypair_for_alias(alias: str) -> Keypair:
    if alias in _KEYPAIR_CACHE:
        return _KEYPAIR_CACHE[alias]
    path = _alias_to_path(alias)
    if not path.exists():
        raise ValueError(f"Dev signer keypair not found: {path}")
    kp = _load_keypair(path)
    _KEYPAIR_CACHE[alias] = kp
    return kp


def wallet_address_for_subject(subject: str | None) -> str:
    alias = normalize_dev_subject(subject)
    return str(get_keypair_for_alias(alias).pubkey())


def wallet_address_for_user(user: User) -> str:
    return wallet_address_for_subject(user.privy_user_id)


async def sign_and_send_unsigned_tx_for_user(
    user: User,
    unsigned_tx_bytes: bytes,
    rpc_url: str,
    retries: int,
) -> str:
    alias = normalize_dev_subject(user.privy_user_id)
    keypair = get_keypair_for_alias(alias)
    expected_signer = str(keypair.pubkey())

    tx = Transaction.from_bytes(unsigned_tx_bytes)
    signer_keys = {str(pk) for pk in tx.message.signer_keys()}
    if expected_signer not in signer_keys:
        raise ValueError(
            f"Unsigned tx signer mismatch. expected={expected_signer} "
            f"message_signers={sorted(signer_keys)}"
        )

    tx.sign([keypair], tx.message.recent_blockhash)
    return await solana_tx.send_and_confirm_transaction(tx, rpc_url, retries=retries)

