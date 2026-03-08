#!/usr/bin/env python3
"""Read and decode config PDA via raw RPC (no IDL dependency)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import sys
import urllib.request

from solders.pubkey import Pubkey


def _required(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


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


def _decode_config(raw: bytes) -> dict:
    if len(raw) < 8 + 32 + 32 + 32 + 2 + 8 + 8 + 8 + 1:
        raise RuntimeError(f"Config account too small: {len(raw)} bytes")

    discr = raw[:8]
    expected_discr = hashlib.sha256(b"account:Config").digest()[:8]

    off = 8
    admin = Pubkey.from_bytes(raw[off : off + 32]); off += 32
    skr_mint = Pubkey.from_bytes(raw[off : off + 32]); off += 32
    treasury_wallet = Pubkey.from_bytes(raw[off : off + 32]); off += 32
    fee_bps = struct.unpack_from("<H", raw, off)[0]; off += 2
    min_bet = struct.unpack_from("<Q", raw, off)[0]; off += 8
    max_bet = struct.unpack_from("<Q", raw, off)[0]; off += 8
    match_counter = struct.unpack_from("<Q", raw, off)[0]; off += 8
    paused = raw[off] != 0

    return {
        "discriminator_hex": discr.hex(),
        "discriminator_matches": discr == expected_discr,
        "admin": str(admin),
        "skr_mint": str(skr_mint),
        "treasury_wallet": str(treasury_wallet),
        "fee_bps": fee_bps,
        "min_bet": min_bet,
        "max_bet": max_bet,
        "match_counter": match_counter,
        "paused": paused,
    }


def main() -> int:
    rpc_url = os.getenv("SOLANA_URL", "https://api.devnet.solana.com")
    program_id = Pubkey.from_string(_required("BETTING_PROGRAM_ID"))
    config_pda, _ = Pubkey.find_program_address([b"config"], program_id)

    result = _rpc(rpc_url, "getAccountInfo", [str(config_pda), {"encoding": "base64"}])
    val = result.get("value")
    if not val:
        raise RuntimeError(f"Config account not found: {config_pda}")

    raw = base64.b64decode(val["data"][0])
    decoded = _decode_config(raw)

    print(json.dumps({"config_pda": str(config_pda), "config": decoded}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
