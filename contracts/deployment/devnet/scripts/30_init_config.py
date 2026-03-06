#!/usr/bin/env python3
"""Initialize config PDA via raw RPC + solders (no IDL dependency)."""

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

TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")


def _required(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


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


def main() -> int:
    rpc_url = os.getenv("SOLANA_URL", "https://api.devnet.solana.com")

    admin_keypair_path = _required("ADMIN_KEYPAIR")
    program_id = Pubkey.from_string(_required("BETTING_PROGRAM_ID"))
    skr_mint = Pubkey.from_string(_required("SKR_MINT"))
    treasury_wallet = Pubkey.from_string(_required("TREASURY_WALLET"))
    min_bet = int(_required("MIN_BET_BASE_UNITS"))
    max_bet = int(_required("MAX_BET_BASE_UNITS"))

    admin = _load_keypair(admin_keypair_path)

    config_pda, _ = Pubkey.find_program_address([b"config"], program_id)

    # Anchor discriminator: sha256("global:init_config")[:8]
    disc = hashlib.sha256(b"global:init_config").digest()[:8]
    data = disc + struct.pack("<Q", min_bet) + struct.pack("<Q", max_bet)

    ix = Instruction(
        program_id,
        data,
        [
            AccountMeta(config_pda, is_signer=False, is_writable=True),
            AccountMeta(admin.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(skr_mint, is_signer=False, is_writable=False),
            AccountMeta(treasury_wallet, is_signer=False, is_writable=False),
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )

    blockhash = _rpc(rpc_url, "getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
    msg = Message.new_with_blockhash([ix], admin.pubkey(), Hash.from_string(blockhash))
    tx = Transaction([admin], msg, Hash.from_string(blockhash))

    tx_b64 = base64.b64encode(bytes(tx)).decode("utf-8")
    sig = _rpc(
        rpc_url,
        "sendTransaction",
        [tx_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
    )

    print(f"init_config signature: {sig}")
    print(f"config PDA: {config_pda}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
