#!/usr/bin/env python3
"""Update config PDA via raw RPC + solders (no IDL dependency)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import sys
import urllib.request

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction


def _required(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _optional(name: str) -> str | None:
    val = os.getenv(name, "").strip()
    return val or None


def _optional_int(name: str) -> int | None:
    raw = _optional(name)
    if raw is None:
        return None
    return int(raw)


def _as_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _load_keypair(path: str) -> Keypair:
    with open(path, "r", encoding="utf-8") as f:
        arr = json.load(f)
    if not isinstance(arr, list) or len(arr) != 64:
        raise RuntimeError(f"Invalid keypair file format: {path}")
    return Keypair.from_bytes(bytes(arr))


def _rpc(rpc_url: str, method: str, params: list) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    if "error" in out:
        raise RuntimeError(f"RPC {method} error: {out['error']}")
    return out["result"]


def _opt_pubkey(val: Pubkey | None) -> bytes:
    return b"\x00" if val is None else b"\x01" + bytes(val)


def _opt_u16(val: int | None) -> bytes:
    return b"\x00" if val is None else b"\x01" + struct.pack("<H", val)


def _opt_u64(val: int | None) -> bytes:
    return b"\x00" if val is None else b"\x01" + struct.pack("<Q", val)


def main() -> int:
    rpc_url = os.getenv("SOLANA_URL", "https://api.devnet.solana.com")
    dry_run = _as_bool("DRY_RUN", default=False)

    admin_keypair_path = _required("ADMIN_KEYPAIR")
    program_id = Pubkey.from_string(_required("BETTING_PROGRAM_ID"))
    admin = _load_keypair(admin_keypair_path)

    # Optional fields supported by update_config.
    new_admin_raw = _optional("NEW_ADMIN")
    new_treasury_raw = _optional("NEW_TREASURY_WALLET") or _optional("NEW_TREASURY")
    new_fee_bps = _optional_int("NEW_FEE_BPS")

    # Bet range update values (fallback to init-style names for convenience).
    new_min_bet = _optional_int("NEW_MIN_BET_BASE_UNITS")
    if new_min_bet is None:
        new_min_bet = _optional_int("MIN_BET_BASE_UNITS")

    new_max_bet = _optional_int("NEW_MAX_BET_BASE_UNITS")
    if new_max_bet is None:
        new_max_bet = _optional_int("MAX_BET_BASE_UNITS")

    new_admin = Pubkey.from_string(new_admin_raw) if new_admin_raw else None
    new_treasury_wallet = Pubkey.from_string(new_treasury_raw) if new_treasury_raw else None

    if all(v is None for v in (new_admin, new_treasury_wallet, new_fee_bps, new_min_bet, new_max_bet)):
        raise RuntimeError(
            "No updates provided. Set at least one of: "
            "NEW_ADMIN, NEW_TREASURY_WALLET, NEW_FEE_BPS, NEW_MIN_BET_BASE_UNITS, NEW_MAX_BET_BASE_UNITS."
        )

    if new_fee_bps is not None and not (0 <= new_fee_bps <= 1000):
        raise RuntimeError("NEW_FEE_BPS must be between 0 and 1000")
    if (new_min_bet is not None and new_min_bet < 0) or (new_max_bet is not None and new_max_bet < 0):
        raise RuntimeError("NEW_MIN_BET_BASE_UNITS and NEW_MAX_BET_BASE_UNITS must be >= 0")
    if new_min_bet is not None and new_max_bet is not None and new_min_bet > new_max_bet:
        raise RuntimeError("NEW_MIN_BET_BASE_UNITS cannot be greater than NEW_MAX_BET_BASE_UNITS")

    config_pda, _ = Pubkey.find_program_address([b"config"], program_id)

    # Anchor discriminator: sha256("global:update_config")[:8]
    disc = hashlib.sha256(b"global:update_config").digest()[:8]
    data = (
        disc
        + _opt_pubkey(new_admin)
        + _opt_pubkey(new_treasury_wallet)
        + _opt_u16(new_fee_bps)
        + _opt_u64(new_min_bet)
        + _opt_u64(new_max_bet)
    )

    ix = Instruction(
        program_id,
        data,
        [
            AccountMeta(config_pda, is_signer=False, is_writable=True),
            AccountMeta(admin.pubkey(), is_signer=True, is_writable=False),
        ],
    )

    print("update_config request:")
    print(f"  rpc_url:             {rpc_url}")
    print(f"  program_id:          {program_id}")
    print(f"  config_pda:          {config_pda}")
    print(f"  admin_signer:        {admin.pubkey()}")
    print(f"  new_admin:           {new_admin}")
    print(f"  new_treasury_wallet: {new_treasury_wallet}")
    print(f"  new_fee_bps:         {new_fee_bps}")
    print(f"  new_min_bet:         {new_min_bet}")
    print(f"  new_max_bet:         {new_max_bet}")

    if dry_run:
        print("DRY_RUN=1 set; not sending transaction.")
        return 0

    blockhash = _rpc(rpc_url, "getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
    msg = Message.new_with_blockhash([ix], admin.pubkey(), Hash.from_string(blockhash))
    tx = Transaction([admin], msg, Hash.from_string(blockhash))

    tx_b64 = base64.b64encode(bytes(tx)).decode("utf-8")
    sig = _rpc(
        rpc_url,
        "sendTransaction",
        [tx_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
    )

    print(f"update_config signature: {sig}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

