#!/usr/bin/env python3
"""Live devnet E2E test: create_match -> place_bet -> lock -> resolve -> claim."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
RENT_SYSVAR_ID = Pubkey.from_string("SysvarRent111111111111111111111111111111111")


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
    retry_delay = 0.4
    for attempt in range(10):
        try:
            req = urllib.request.Request(
                rpc_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            if "error" in out:
                err = out["error"]
                # Retry rate-limit / transient backend overload responses.
                code = err.get("code")
                msg = str(err.get("message", ""))
                if code in (429, -32005) or "Too Many Requests" in msg:
                    if attempt < 9:
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 1.8, 4.0)
                        continue
                raise RuntimeError(f"RPC {method} error: {err}")
            return out["result"]
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 9:
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.8, 4.0)
                continue
            raise RuntimeError(f"RPC {method} HTTP error: {exc}") from exc
        except urllib.error.URLError as exc:
            if attempt < 9:
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.8, 4.0)
                continue
            raise RuntimeError(f"RPC {method} URL error: {exc}") from exc

    raise RuntimeError(f"RPC {method} failed after retries")


def _confirm_sig(rpc_url: str, sig: str, timeout_sec: int = 45) -> None:
    end = time.time() + timeout_sec
    while time.time() < end:
        st = _rpc(
            rpc_url,
            "getSignatureStatuses",
            [[sig], {"searchTransactionHistory": True}],
        )["value"][0]
        if st is not None:
            if st.get("err") is not None:
                raise RuntimeError(f"Transaction failed: {sig} err={st['err']}")
            if st.get("confirmationStatus") in ("confirmed", "finalized"):
                return
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for transaction confirmation: {sig}")


def _send_tx(rpc_url: str, payer: Keypair, signers: list[Keypair], instructions: list[Instruction]) -> str:
    signer_map = {str(payer.pubkey()): payer}
    for s in signers:
        signer_map[str(s.pubkey())] = s
    tx_signers = list(signer_map.values())

    blockhash = _rpc(rpc_url, "getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
    recent = Hash.from_string(blockhash)
    msg = Message.new_with_blockhash(instructions, payer.pubkey(), recent)
    tx = Transaction(tx_signers, msg, recent)
    tx_b64 = base64.b64encode(bytes(tx)).decode("utf-8")

    sig = _rpc(
        rpc_url,
        "sendTransaction",
        [tx_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
    )
    _confirm_sig(rpc_url, sig)
    return sig


def _ix_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode("utf-8")).digest()[:8]


def _ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    ata, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    return ata


def _get_account_info(rpc_url: str, key: Pubkey) -> dict | None:
    return _rpc(
        rpc_url,
        "getAccountInfo",
        [str(key), {"encoding": "base64", "commitment": "confirmed"}],
    )["value"]


def _get_token_amount(rpc_url: str, token_account: Pubkey) -> int:
    out = _rpc(rpc_url, "getTokenAccountBalance", [str(token_account), {"commitment": "confirmed"}])
    return int(out["value"]["amount"])


def _decode_config(raw: bytes) -> dict:
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
        "admin": admin,
        "skr_mint": skr_mint,
        "treasury_wallet": treasury_wallet,
        "fee_bps": fee_bps,
        "min_bet": min_bet,
        "max_bet": max_bet,
        "match_counter": match_counter,
        "paused": paused,
    }


def _decode_match(raw: bytes) -> dict:
    off = 8
    match_id = struct.unpack_from("<Q", raw, off)[0]; off += 8
    status = raw[off]; off += 1
    off += 32  # model_a_hash
    off += 32  # model_b_hash
    total_a = struct.unpack_from("<Q", raw, off)[0]; off += 8
    total_b = struct.unpack_from("<Q", raw, off)[0]; off += 8
    winner = raw[off]; off += 1
    fee_amount = struct.unpack_from("<Q", raw, off)[0]; off += 8
    payout_pool = struct.unpack_from("<Q", raw, off)[0]; off += 8
    winning_total = struct.unpack_from("<Q", raw, off)[0]; off += 8
    claimed_winning_total = struct.unpack_from("<Q", raw, off)[0]; off += 8
    refunded_total = struct.unpack_from("<Q", raw, off)[0]; off += 8
    vault_authority = Pubkey.from_bytes(raw[off : off + 32]); off += 32
    vault_ata = Pubkey.from_bytes(raw[off : off + 32]); off += 32
    created_at = struct.unpack_from("<q", raw, off)[0]; off += 8
    locked_at = struct.unpack_from("<q", raw, off)[0]; off += 8
    resolved_at = struct.unpack_from("<q", raw, off)[0]; off += 8
    return {
        "id": match_id,
        "status": status,
        "total_a": total_a,
        "total_b": total_b,
        "winner": winner,
        "fee_amount": fee_amount,
        "payout_pool": payout_pool,
        "winning_total": winning_total,
        "claimed_winning_total": claimed_winning_total,
        "refunded_total": refunded_total,
        "vault_authority": vault_authority,
        "vault_ata": vault_ata,
        "created_at": created_at,
        "locked_at": locked_at,
        "resolved_at": resolved_at,
    }


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def main() -> int:
    rpc_url = os.getenv("SOLANA_URL", "https://api.devnet.solana.com")
    program_id = Pubkey.from_string(_required("BETTING_PROGRAM_ID"))
    skr_mint = Pubkey.from_string(_required("SKR_MINT"))

    deployer = _load_keypair(_required("DEPLOYER_KEYPAIR"))
    admin = _load_keypair(_required("ADMIN_KEYPAIR"))
    user1 = _load_keypair(_required("USER1_KEYPAIR"))
    user2 = _load_keypair(_required("USER2_KEYPAIR"))

    config_pda, _ = Pubkey.find_program_address([b"config"], program_id)
    cfg_info = _get_account_info(rpc_url, config_pda)
    if not cfg_info:
        raise RuntimeError(f"Config account not found: {config_pda}")
    cfg_raw = base64.b64decode(cfg_info["data"][0])
    cfg = _decode_config(cfg_raw)
    _expect(cfg["skr_mint"] == skr_mint, "Config mint does not match env SKR_MINT")
    _expect(not cfg["paused"], "System is paused; cannot place bets")

    treasury_wallet = cfg["treasury_wallet"]
    treasury_ata = _ata(treasury_wallet, skr_mint)

    # Pick test bet sizes within configured bounds.
    bet_a = int(os.getenv("BET_A_BASE_UNITS", "300"))
    bet_b = int(os.getenv("BET_B_BASE_UNITS", "200"))
    bet_a = max(cfg["min_bet"], min(cfg["max_bet"], bet_a))
    bet_b = max(cfg["min_bet"], min(cfg["max_bet"], bet_b))

    match_id = cfg["match_counter"]
    match_pda, _ = Pubkey.find_program_address([b"match", struct.pack("<Q", match_id)], program_id)
    vault_authority, _ = Pubkey.find_program_address([b"vault_auth", bytes(match_pda)], program_id)
    vault_ata = _ata(vault_authority, skr_mint)
    user1_ata = _ata(user1.pubkey(), skr_mint)
    user2_ata = _ata(user2.pubkey(), skr_mint)
    bet1_pda, _ = Pubkey.find_program_address([b"bet", bytes(match_pda), bytes(user1.pubkey())], program_id)
    bet2_pda, _ = Pubkey.find_program_address([b"bet", bytes(match_pda), bytes(user2.pubkey())], program_id)

    # Sanity: token accounts should exist because shell wrapper funds recipients.
    _expect(_get_account_info(rpc_url, user1_ata) is not None, f"user1 ATA missing: {user1_ata}")
    _expect(_get_account_info(rpc_url, user2_ata) is not None, f"user2 ATA missing: {user2_ata}")
    _expect(_get_account_info(rpc_url, treasury_ata) is not None, f"treasury ATA missing: {treasury_ata}")

    # create_match
    model_a = bytes([int(match_id % 251)]) * 32
    model_b = bytes([int((match_id + 113) % 251)]) * 32
    ix_create = Instruction(
        program_id,
        _ix_discriminator("create_match") + model_a + model_b,
        [
            AccountMeta(config_pda, is_signer=False, is_writable=True),
            AccountMeta(match_pda, is_signer=False, is_writable=True),
            AccountMeta(vault_authority, is_signer=False, is_writable=False),
            AccountMeta(vault_ata, is_signer=False, is_writable=True),
            AccountMeta(skr_mint, is_signer=False, is_writable=False),
            AccountMeta(admin.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(ASSOCIATED_TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(RENT_SYSVAR_ID, is_signer=False, is_writable=False),
        ],
    )
    sig_create = _send_tx(rpc_url, admin, [admin], [ix_create])

    # place_bet user1 on side A (enum index 1)
    ix_bet1 = Instruction(
        program_id,
        _ix_discriminator("place_bet") + bytes([1]) + struct.pack("<Q", bet_a),
        [
            AccountMeta(config_pda, is_signer=False, is_writable=False),
            AccountMeta(match_pda, is_signer=False, is_writable=True),
            AccountMeta(bet1_pda, is_signer=False, is_writable=True),
            AccountMeta(user1_ata, is_signer=False, is_writable=True),
            AccountMeta(vault_ata, is_signer=False, is_writable=True),
            AccountMeta(vault_authority, is_signer=False, is_writable=False),
            AccountMeta(skr_mint, is_signer=False, is_writable=False),
            AccountMeta(user1.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(ASSOCIATED_TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    sig_bet1 = _send_tx(rpc_url, user1, [user1], [ix_bet1])

    # place_bet user2 on side B (enum index 2)
    ix_bet2 = Instruction(
        program_id,
        _ix_discriminator("place_bet") + bytes([2]) + struct.pack("<Q", bet_b),
        [
            AccountMeta(config_pda, is_signer=False, is_writable=False),
            AccountMeta(match_pda, is_signer=False, is_writable=True),
            AccountMeta(bet2_pda, is_signer=False, is_writable=True),
            AccountMeta(user2_ata, is_signer=False, is_writable=True),
            AccountMeta(vault_ata, is_signer=False, is_writable=True),
            AccountMeta(vault_authority, is_signer=False, is_writable=False),
            AccountMeta(skr_mint, is_signer=False, is_writable=False),
            AccountMeta(user2.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(ASSOCIATED_TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    sig_bet2 = _send_tx(rpc_url, user2, [user2], [ix_bet2])

    # lock_match
    ix_lock = Instruction(
        program_id,
        _ix_discriminator("lock_match"),
        [
            AccountMeta(config_pda, is_signer=False, is_writable=False),
            AccountMeta(match_pda, is_signer=False, is_writable=True),
            AccountMeta(admin.pubkey(), is_signer=True, is_writable=False),
        ],
    )
    sig_lock = _send_tx(rpc_url, admin, [admin], [ix_lock])

    treasury_before_resolve = _get_token_amount(rpc_url, treasury_ata)

    # resolve_match winner A (enum index 1)
    ix_resolve = Instruction(
        program_id,
        _ix_discriminator("resolve_match") + bytes([1]),
        [
            AccountMeta(config_pda, is_signer=False, is_writable=False),
            AccountMeta(match_pda, is_signer=False, is_writable=True),
            AccountMeta(vault_ata, is_signer=False, is_writable=True),
            AccountMeta(vault_authority, is_signer=False, is_writable=True),
            AccountMeta(treasury_ata, is_signer=False, is_writable=True),
            AccountMeta(skr_mint, is_signer=False, is_writable=False),
            AccountMeta(admin.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    sig_resolve = _send_tx(rpc_url, admin, [admin], [ix_resolve])

    match_info = _get_account_info(rpc_url, match_pda)
    _expect(match_info is not None, "Match account missing after resolve")
    match_dec = _decode_match(base64.b64decode(match_info["data"][0]))
    _expect(match_dec["status"] == 2, f"Unexpected status after resolve: {match_dec['status']}")
    _expect(match_dec["winner"] == 1, f"Unexpected winner after resolve: {match_dec['winner']}")
    _expect(match_dec["total_a"] == bet_a, f"total_a mismatch: {match_dec['total_a']} != {bet_a}")
    _expect(match_dec["total_b"] == bet_b, f"total_b mismatch: {match_dec['total_b']} != {bet_b}")

    pool = bet_a + bet_b
    expected_fee = (pool * cfg["fee_bps"]) // 10_000
    _expect(match_dec["fee_amount"] == expected_fee, f"fee mismatch: {match_dec['fee_amount']} != {expected_fee}")
    _expect(match_dec["payout_pool"] == pool - expected_fee, "payout_pool mismatch")
    _expect(match_dec["winning_total"] == bet_a, "winning_total mismatch")

    treasury_after_resolve = _get_token_amount(rpc_url, treasury_ata)
    _expect(
        treasury_after_resolve - treasury_before_resolve == expected_fee,
        f"treasury fee transfer mismatch: {treasury_after_resolve - treasury_before_resolve} != {expected_fee}",
    )

    user1_before_claim = _get_token_amount(rpc_url, user1_ata)

    # claim (winner user1)
    ix_claim = Instruction(
        program_id,
        _ix_discriminator("claim"),
        [
            AccountMeta(config_pda, is_signer=False, is_writable=False),
            AccountMeta(match_pda, is_signer=False, is_writable=True),
            AccountMeta(bet1_pda, is_signer=False, is_writable=True),
            AccountMeta(user1_ata, is_signer=False, is_writable=True),
            AccountMeta(vault_ata, is_signer=False, is_writable=True),
            AccountMeta(vault_authority, is_signer=False, is_writable=True),
            AccountMeta(treasury_ata, is_signer=False, is_writable=True),
            AccountMeta(treasury_wallet, is_signer=False, is_writable=True),
            AccountMeta(admin.pubkey(), is_signer=False, is_writable=True),
            AccountMeta(skr_mint, is_signer=False, is_writable=False),
            AccountMeta(user1.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )
    sig_claim = _send_tx(rpc_url, user1, [user1], [ix_claim])

    user1_after_claim = _get_token_amount(rpc_url, user1_ata)
    expected_payout = (match_dec["payout_pool"] * bet_a) // match_dec["winning_total"]
    _expect(
        user1_after_claim - user1_before_claim == expected_payout,
        f"claim payout mismatch: {user1_after_claim - user1_before_claim} != {expected_payout}",
    )

    # Match should auto-close on last winning claim.
    _expect(_get_account_info(rpc_url, match_pda) is None, "Match account not closed after final claim")

    # Losing user's bet should still exist until close_losing_bet is called.
    _expect(_get_account_info(rpc_url, bet2_pda) is not None, "Losing bet PDA unexpectedly closed")

    out = {
        "pass": True,
        "program_id": str(program_id),
        "config_pda": str(config_pda),
        "match_id": match_id,
        "match_pda": str(match_pda),
        "vault_authority": str(vault_authority),
        "vault_ata": str(vault_ata),
        "treasury_ata": str(treasury_ata),
        "user1": str(user1.pubkey()),
        "user2": str(user2.pubkey()),
        "bets": {"user1_a": bet_a, "user2_b": bet_b},
        "expected_fee": expected_fee,
        "expected_payout_user1": expected_payout,
        "signatures": {
            "create_match": sig_create,
            "place_bet_user1": sig_bet1,
            "place_bet_user2": sig_bet2,
            "lock_match": sig_lock,
            "resolve_match": sig_resolve,
            "claim_user1": sig_claim,
        },
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        raise SystemExit(1)
