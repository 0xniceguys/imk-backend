#!/usr/bin/env python3
"""Comprehensive live devnet test matrix for the deployed contract."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

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

# WinnerSide enum indices (borsh enum)
SIDE_NONE = 0
SIDE_A = 1
SIDE_B = 2

# MatchStatus enum indices (borsh enum)
STATUS_OPEN = 0
STATUS_LOCKED = 1
STATUS_RESOLVED = 2
STATUS_CANCELLED = 3


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


def _rpc(rpc_url: str, method: str, params: list[Any]) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    delay = 0.35
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
                code = err.get("code")
                msg = str(err.get("message", ""))
                if code in (429, -32005) or "Too Many Requests" in msg:
                    if attempt < 9:
                        time.sleep(delay)
                        delay = min(delay * 1.8, 4.0)
                        continue
                raise RuntimeError(f"RPC {method} error: {err}")
            return out["result"]
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 9:
                time.sleep(delay)
                delay = min(delay * 1.8, 4.0)
                continue
            raise RuntimeError(f"RPC {method} HTTP error: {exc}") from exc
        except urllib.error.URLError as exc:
            if attempt < 9:
                time.sleep(delay)
                delay = min(delay * 1.8, 4.0)
                continue
            raise RuntimeError(f"RPC {method} URL error: {exc}") from exc

    raise RuntimeError(f"RPC {method} failed after retries")


def _ix_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode("utf-8")).digest()[:8]


def _ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    ata, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    return ata


def _opt_pubkey(val: Pubkey | None) -> bytes:
    return b"\x00" if val is None else b"\x01" + bytes(val)


def _opt_u16(val: int | None) -> bytes:
    return b"\x00" if val is None else b"\x01" + struct.pack("<H", val)


def _opt_u64(val: int | None) -> bytes:
    return b"\x00" if val is None else b"\x01" + struct.pack("<Q", val)


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def _decode_config(raw: bytes) -> dict[str, Any]:
    if len(raw) < 8 + 32 + 32 + 32 + 2 + 8 + 8 + 8 + 1:
        raise RuntimeError(f"Config account too small: {len(raw)}")
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


def _decode_match(raw: bytes) -> dict[str, Any]:
    # Serialized Match payload is 225 bytes + 8-byte discriminator = 233 bytes.
    if len(raw) < 233:
        raise RuntimeError(f"Match account too small: {len(raw)}")
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


@dataclass
class Ctx:
    rpc_url: str
    program_id: Pubkey
    skr_mint: Pubkey
    deployer: Keypair
    admin: Keypair
    u1: Keypair
    u2: Keypair
    u3: Keypair
    intruder: Keypair
    payer: Keypair
    config_pda: Pubkey

    def account_info(self, key: Pubkey) -> dict | None:
        return _rpc(
            self.rpc_url,
            "getAccountInfo",
            [str(key), {"encoding": "base64", "commitment": "confirmed"}],
        )["value"]

    def account_exists(self, key: Pubkey) -> bool:
        return self.account_info(key) is not None

    def wait_account(self, key: Pubkey, should_exist: bool, timeout_sec: int = 20) -> None:
        end = time.time() + timeout_sec
        while time.time() < end:
            exists = self.account_exists(key)
            if exists == should_exist:
                return
            time.sleep(0.4)
        state = "exist" if should_exist else "be closed"
        raise RuntimeError(f"Timed out waiting for account to {state}: {key}")

    def token_amount(self, token_account: Pubkey) -> int:
        out = _rpc(
            self.rpc_url,
            "getTokenAccountBalance",
            [str(token_account), {"commitment": "confirmed"}],
        )
        return int(out["value"]["amount"])

    def fetch_config(self) -> dict[str, Any]:
        info = self.account_info(self.config_pda)
        if not info:
            raise RuntimeError(f"Config account missing: {self.config_pda}")
        raw = base64.b64decode(info["data"][0])
        return _decode_config(raw)

    def fetch_match(self, match_pda: Pubkey) -> dict[str, Any]:
        info = self.account_info(match_pda)
        if not info:
            raise RuntimeError(f"Match account missing: {match_pda}")
        raw = base64.b64decode(info["data"][0])
        return _decode_match(raw)

    def build_tx(self, payer: Keypair, signers: list[Keypair], instructions: list[Instruction]) -> str:
        signer_map = {str(payer.pubkey()): payer}
        for s in signers:
            signer_map[str(s.pubkey())] = s
        tx_signers = list(signer_map.values())
        blockhash = _rpc(self.rpc_url, "getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
        recent = Hash.from_string(blockhash)
        msg = Message.new_with_blockhash(instructions, payer.pubkey(), recent)
        tx = Transaction(tx_signers, msg, recent)
        return base64.b64encode(bytes(tx)).decode("utf-8")

    def _wait_sig(self, sig: str, expect_success: bool, timeout_sec: int = 45) -> dict:
        end = time.time() + timeout_sec
        while time.time() < end:
            st = _rpc(
                self.rpc_url,
                "getSignatureStatuses",
                [[sig], {"searchTransactionHistory": True}],
            )["value"][0]
            if st is not None:
                err = st.get("err")
                status = st.get("confirmationStatus")
                if expect_success and err is not None:
                    raise RuntimeError(f"Transaction failed: {sig} err={err}")
                if not expect_success and err is not None:
                    return st
                if status in ("confirmed", "finalized"):
                    if expect_success:
                        return st
                    raise RuntimeError(f"Expected failure but transaction succeeded: {sig}")
            time.sleep(0.45)
        raise RuntimeError(f"Timed out waiting for transaction confirmation: {sig}")

    def send_ok(self, payer: Keypair, signers: list[Keypair], instructions: list[Instruction]) -> str:
        tx_b64 = self.build_tx(payer, signers, instructions)
        sig = _rpc(
            self.rpc_url,
            "sendTransaction",
            [tx_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
        )
        self._wait_sig(sig, expect_success=True)
        return sig

    def send_fail(self, payer: Keypair, signers: list[Keypair], instructions: list[Instruction]) -> str:
        tx_b64 = self.build_tx(payer, signers, instructions)
        try:
            sig = _rpc(
                self.rpc_url,
                "sendTransaction",
                [tx_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
            )
        except Exception as exc:
            # Preflight-level failure is acceptable for negative tests.
            return f"preflight:{exc}"
        st = self._wait_sig(sig, expect_success=False)
        return f"chain:{sig}:{st.get('err')}"

    def derive_next_match(self) -> dict[str, Any]:
        cfg = self.fetch_config()
        match_id = int(cfg["match_counter"])
        match_pda, _ = Pubkey.find_program_address([b"match", struct.pack("<Q", match_id)], self.program_id)
        vault_auth, _ = Pubkey.find_program_address([b"vault_auth", bytes(match_pda)], self.program_id)
        vault_ata = _ata(vault_auth, self.skr_mint)
        return {
            "match_id": match_id,
            "match_pda": match_pda,
            "vault_auth": vault_auth,
            "vault_ata": vault_ata,
        }

    # Instruction builders.
    def ix_update_config(
        self,
        signer: Pubkey,
        new_admin: Pubkey | None,
        new_treasury: Pubkey | None,
        new_fee_bps: int | None,
        new_min_bet: int | None,
        new_max_bet: int | None,
    ) -> Instruction:
        data = (
            _ix_discriminator("update_config")
            + _opt_pubkey(new_admin)
            + _opt_pubkey(new_treasury)
            + _opt_u16(new_fee_bps)
            + _opt_u64(new_min_bet)
            + _opt_u64(new_max_bet)
        )
        return Instruction(
            self.program_id,
            data,
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=True),
                AccountMeta(signer, is_signer=True, is_writable=False),
            ],
        )

    def ix_set_paused(self, signer: Pubkey, paused: bool) -> Instruction:
        data = _ix_discriminator("set_paused") + (b"\x01" if paused else b"\x00")
        return Instruction(
            self.program_id,
            data,
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=True),
                AccountMeta(signer, is_signer=True, is_writable=False),
            ],
        )

    def ix_create_match(self, md: dict[str, Any], signer: Pubkey) -> Instruction:
        a = bytes([md["match_id"] % 251]) * 32
        b = bytes([(md["match_id"] + 119) % 251]) * 32
        data = _ix_discriminator("create_match") + a + b
        return Instruction(
            self.program_id,
            data,
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=True),
                AccountMeta(md["match_pda"], is_signer=False, is_writable=True),
                AccountMeta(md["vault_auth"], is_signer=False, is_writable=False),
                AccountMeta(md["vault_ata"], is_signer=False, is_writable=True),
                AccountMeta(self.skr_mint, is_signer=False, is_writable=False),
                AccountMeta(signer, is_signer=True, is_writable=True),
                AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(ASSOCIATED_TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(RENT_SYSVAR_ID, is_signer=False, is_writable=False),
            ],
        )

    def ix_place_bet(self, md: dict[str, Any], user: Pubkey, side: int, amount: int) -> Instruction:
        user_bet, _ = Pubkey.find_program_address([b"bet", bytes(md["match_pda"]), bytes(user)], self.program_id)
        user_ata = _ata(user, self.skr_mint)
        data = _ix_discriminator("place_bet") + bytes([side]) + struct.pack("<Q", amount)
        return Instruction(
            self.program_id,
            data,
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=False),
                AccountMeta(md["match_pda"], is_signer=False, is_writable=True),
                AccountMeta(user_bet, is_signer=False, is_writable=True),
                AccountMeta(user_ata, is_signer=False, is_writable=True),
                AccountMeta(md["vault_ata"], is_signer=False, is_writable=True),
                AccountMeta(md["vault_auth"], is_signer=False, is_writable=False),
                AccountMeta(self.skr_mint, is_signer=False, is_writable=False),
                AccountMeta(user, is_signer=True, is_writable=True),
                AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(ASSOCIATED_TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )

    def ix_lock_match(self, md: dict[str, Any], signer: Pubkey) -> Instruction:
        return Instruction(
            self.program_id,
            _ix_discriminator("lock_match"),
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=False),
                AccountMeta(md["match_pda"], is_signer=False, is_writable=True),
                AccountMeta(signer, is_signer=True, is_writable=False),
            ],
        )

    def ix_resolve_match(self, md: dict[str, Any], signer: Pubkey, winner_side: int, treasury_ata: Pubkey) -> Instruction:
        data = _ix_discriminator("resolve_match") + bytes([winner_side])
        return Instruction(
            self.program_id,
            data,
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=False),
                AccountMeta(md["match_pda"], is_signer=False, is_writable=True),
                AccountMeta(md["vault_ata"], is_signer=False, is_writable=True),
                AccountMeta(md["vault_auth"], is_signer=False, is_writable=True),
                AccountMeta(treasury_ata, is_signer=False, is_writable=True),
                AccountMeta(self.skr_mint, is_signer=False, is_writable=False),
                AccountMeta(signer, is_signer=True, is_writable=True),
                AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )

    def ix_claim(self, md: dict[str, Any], user: Pubkey, treasury_ata: Pubkey, treasury_wallet: Pubkey, admin_pub: Pubkey) -> Instruction:
        user_bet, _ = Pubkey.find_program_address([b"bet", bytes(md["match_pda"]), bytes(user)], self.program_id)
        user_ata = _ata(user, self.skr_mint)
        return Instruction(
            self.program_id,
            _ix_discriminator("claim"),
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=False),
                AccountMeta(md["match_pda"], is_signer=False, is_writable=True),
                AccountMeta(user_bet, is_signer=False, is_writable=True),
                AccountMeta(user_ata, is_signer=False, is_writable=True),
                AccountMeta(md["vault_ata"], is_signer=False, is_writable=True),
                AccountMeta(md["vault_auth"], is_signer=False, is_writable=True),
                AccountMeta(treasury_ata, is_signer=False, is_writable=True),
                AccountMeta(treasury_wallet, is_signer=False, is_writable=True),
                AccountMeta(admin_pub, is_signer=False, is_writable=True),
                AccountMeta(self.skr_mint, is_signer=False, is_writable=False),
                AccountMeta(user, is_signer=True, is_writable=True),
                AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )

    def ix_cancel_match(self, md: dict[str, Any], signer: Pubkey) -> Instruction:
        return Instruction(
            self.program_id,
            _ix_discriminator("cancel_match"),
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=False),
                AccountMeta(md["match_pda"], is_signer=False, is_writable=True),
                AccountMeta(md["vault_ata"], is_signer=False, is_writable=True),
                AccountMeta(md["vault_auth"], is_signer=False, is_writable=True),
                AccountMeta(self.skr_mint, is_signer=False, is_writable=False),
                AccountMeta(signer, is_signer=True, is_writable=True),
                AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )

    def ix_refund_bet(self, md: dict[str, Any], user: Pubkey, admin_pub: Pubkey) -> Instruction:
        user_bet, _ = Pubkey.find_program_address([b"bet", bytes(md["match_pda"]), bytes(user)], self.program_id)
        user_ata = _ata(user, self.skr_mint)
        return Instruction(
            self.program_id,
            _ix_discriminator("refund_bet"),
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=False),
                AccountMeta(md["match_pda"], is_signer=False, is_writable=True),
                AccountMeta(user_bet, is_signer=False, is_writable=True),
                AccountMeta(user_ata, is_signer=False, is_writable=True),
                AccountMeta(md["vault_ata"], is_signer=False, is_writable=True),
                AccountMeta(md["vault_auth"], is_signer=False, is_writable=True),
                AccountMeta(admin_pub, is_signer=False, is_writable=True),
                AccountMeta(self.skr_mint, is_signer=False, is_writable=False),
                AccountMeta(user, is_signer=True, is_writable=True),
                AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )

    def ix_close_losing_bet(self, md: dict[str, Any], user: Pubkey, admin_pub: Pubkey, payer_pub: Pubkey) -> Instruction:
        user_bet, _ = Pubkey.find_program_address([b"bet", bytes(md["match_pda"]), bytes(user)], self.program_id)
        return Instruction(
            self.program_id,
            _ix_discriminator("close_losing_bet"),
            [
                AccountMeta(self.config_pda, is_signer=False, is_writable=False),
                AccountMeta(md["match_pda"], is_signer=False, is_writable=True),
                AccountMeta(user_bet, is_signer=False, is_writable=True),
                AccountMeta(admin_pub, is_signer=False, is_writable=True),
                AccountMeta(payer_pub, is_signer=True, is_writable=True),
            ],
        )


def case_config_pause_and_validation(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {"sigs": [], "fails": []}
    cfg0 = ctx.fetch_config()
    _expect(cfg0["admin"] == ctx.admin.pubkey(), "Configured admin does not match ADMIN_KEYPAIR")

    # Unauthorized update_config.
    r = ctx.send_fail(
        payer=ctx.intruder,
        signers=[ctx.intruder],
        instructions=[ctx.ix_update_config(ctx.intruder.pubkey(), None, None, 300, None, None)],
    )
    out["fails"].append(("unauthorized_update_config", r))

    # Invalid update attempts by admin.
    r = ctx.send_fail(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_update_config(ctx.admin.pubkey(), None, None, 2001, None, None)],
    )
    out["fails"].append(("invalid_fee_bps", r))
    r = ctx.send_fail(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_update_config(ctx.admin.pubkey(), None, None, None, 400, 100)],
    )
    out["fails"].append(("invalid_bet_range", r))

    # Successful config update, then restore later.
    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_update_config(ctx.admin.pubkey(), None, None, 300, 100, 400)],
    )
    out["sigs"].append(("update_config_ok", sig))
    cfg = ctx.fetch_config()
    _expect(cfg["fee_bps"] == 300, "fee_bps update failed")

    # Unauthorized set_paused.
    r = ctx.send_fail(
        payer=ctx.intruder,
        signers=[ctx.intruder],
        instructions=[ctx.ix_set_paused(ctx.intruder.pubkey(), True)],
    )
    out["fails"].append(("unauthorized_set_paused", r))

    # Pause system.
    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_set_paused(ctx.admin.pubkey(), True)],
    )
    out["sigs"].append(("set_paused_true", sig))
    _expect(ctx.fetch_config()["paused"] is True, "paused flag did not set true")

    # Create match while paused should still be allowed.
    md = ctx.derive_next_match()
    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_create_match(md, ctx.admin.pubkey())],
    )
    out["sigs"].append(("create_match_while_paused", sig))
    ctx.wait_account(md["match_pda"], should_exist=True)

    # place_bet should fail while paused.
    r = ctx.send_fail(
        payer=ctx.u1,
        signers=[ctx.u1],
        instructions=[ctx.ix_place_bet(md, ctx.u1.pubkey(), SIDE_A, 300)],
    )
    out["fails"].append(("place_bet_while_paused", r))

    # Unpause and cleanup this no-bet match via cancel.
    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_set_paused(ctx.admin.pubkey(), False)],
    )
    out["sigs"].append(("set_paused_false", sig))
    _expect(ctx.fetch_config()["paused"] is False, "paused flag did not set false")

    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_cancel_match(md, ctx.admin.pubkey())],
    )
    out["sigs"].append(("cancel_empty_paused_match", sig))
    ctx.wait_account(md["match_pda"], should_exist=False)

    # Restore fee to original.
    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[
            ctx.ix_update_config(
                ctx.admin.pubkey(),
                None,
                None,
                int(cfg0["fee_bps"]),
                int(cfg0["min_bet"]),
                int(cfg0["max_bet"]),
            )
        ],
    )
    out["sigs"].append(("restore_config", sig))
    cfg1 = ctx.fetch_config()
    _expect(cfg1["fee_bps"] == cfg0["fee_bps"], "config restore failed (fee_bps)")
    _expect(cfg1["min_bet"] == cfg0["min_bet"], "config restore failed (min_bet)")
    _expect(cfg1["max_bet"] == cfg0["max_bet"], "config restore failed (max_bet)")

    return out


def case_happy_path_and_negative_edges(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {"sigs": [], "fails": []}
    cfg = ctx.fetch_config()
    treasury_wallet: Pubkey = cfg["treasury_wallet"]
    treasury_ata = _ata(treasury_wallet, ctx.skr_mint)

    md = ctx.derive_next_match()

    # Unauthorized create_match by intruder.
    r = ctx.send_fail(
        payer=ctx.intruder,
        signers=[ctx.intruder],
        instructions=[ctx.ix_create_match(md, ctx.intruder.pubkey())],
    )
    out["fails"].append(("unauthorized_create_match", r))

    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_create_match(md, ctx.admin.pubkey())],
    )
    out["sigs"].append(("create_match", sig))

    # Invalid place_bet checks.
    r = ctx.send_fail(
        payer=ctx.u1,
        signers=[ctx.u1],
        instructions=[ctx.ix_place_bet(md, ctx.u1.pubkey(), SIDE_A, int(cfg["min_bet"]) - 1)],
    )
    out["fails"].append(("place_bet_out_of_range_low", r))
    r = ctx.send_fail(
        payer=ctx.u1,
        signers=[ctx.u1],
        instructions=[ctx.ix_place_bet(md, ctx.u1.pubkey(), SIDE_NONE, 300)],
    )
    out["fails"].append(("place_bet_invalid_side", r))

    # Resolve before lock should fail.
    r = ctx.send_fail(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_resolve_match(md, ctx.admin.pubkey(), SIDE_A, treasury_ata)],
    )
    out["fails"].append(("resolve_before_lock", r))

    user1_ata = _ata(ctx.u1.pubkey(), ctx.skr_mint)
    user2_ata = _ata(ctx.u2.pubkey(), ctx.skr_mint)
    treasury_before = ctx.token_amount(treasury_ata)
    user1_before = ctx.token_amount(user1_ata)

    sig = ctx.send_ok(
        payer=ctx.u1,
        signers=[ctx.u1],
        instructions=[ctx.ix_place_bet(md, ctx.u1.pubkey(), SIDE_A, 300)],
    )
    out["sigs"].append(("place_bet_u1_A_300", sig))
    sig = ctx.send_ok(
        payer=ctx.u2,
        signers=[ctx.u2],
        instructions=[ctx.ix_place_bet(md, ctx.u2.pubkey(), SIDE_B, 200)],
    )
    out["sigs"].append(("place_bet_u2_B_200", sig))

    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_lock_match(md, ctx.admin.pubkey())],
    )
    out["sigs"].append(("lock_match", sig))

    r = ctx.send_fail(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_lock_match(md, ctx.admin.pubkey())],
    )
    out["fails"].append(("lock_twice", r))

    sig = ctx.send_ok(
        payer=ctx.admin,
        signers=[ctx.admin],
        instructions=[ctx.ix_resolve_match(md, ctx.admin.pubkey(), SIDE_A, treasury_ata)],
    )
    out["sigs"].append(("resolve_winner_A", sig))

    m = ctx.fetch_match(md["match_pda"])
    _expect(m["status"] == STATUS_RESOLVED, "resolved status mismatch")
    _expect(m["winner"] == SIDE_A, "winner side mismatch")
    _expect(m["fee_amount"] == 25, f"fee mismatch {m['fee_amount']} != 25")
    _expect(m["payout_pool"] == 475, f"payout pool mismatch {m['payout_pool']} != 475")
    _expect(m["winning_total"] == 300, f"winning_total mismatch {m['winning_total']} != 300")

    treasury_after_resolve = ctx.token_amount(treasury_ata)
    _expect(treasury_after_resolve - treasury_before == 25, "treasury fee transfer mismatch")

    # Loser claim should fail.
    r = ctx.send_fail(
        payer=ctx.u2,
        signers=[ctx.u2],
        instructions=[ctx.ix_claim(md, ctx.u2.pubkey(), treasury_ata, treasury_wallet, ctx.admin.pubkey())],
    )
    out["fails"].append(("claim_by_loser", r))

    # close_losing_bet on winner should fail.
    r = ctx.send_fail(
        payer=ctx.payer,
        signers=[ctx.payer],
        instructions=[ctx.ix_close_losing_bet(md, ctx.u1.pubkey(), ctx.admin.pubkey(), ctx.payer.pubkey())],
    )
    out["fails"].append(("close_losing_on_winner_should_fail", r))

    # close_losing_bet on actual loser should pass.
    sig = ctx.send_ok(
        payer=ctx.payer,
        signers=[ctx.payer],
        instructions=[ctx.ix_close_losing_bet(md, ctx.u2.pubkey(), ctx.admin.pubkey(), ctx.payer.pubkey())],
    )
    out["sigs"].append(("close_losing_bet_u2", sig))
    loser_bet, _ = Pubkey.find_program_address([b"bet", bytes(md["match_pda"]), bytes(ctx.u2.pubkey())], ctx.program_id)
    ctx.wait_account(loser_bet, should_exist=False)

    # Winner claim should pay and auto-close match.
    sig = ctx.send_ok(
        payer=ctx.u1,
        signers=[ctx.u1],
        instructions=[ctx.ix_claim(md, ctx.u1.pubkey(), treasury_ata, treasury_wallet, ctx.admin.pubkey())],
    )
    out["sigs"].append(("claim_u1", sig))
    user1_after = ctx.token_amount(user1_ata)
    _expect(user1_after - user1_before == 175, f"user1 net mismatch after round: {user1_after - user1_before} != 175")
    ctx.wait_account(md["match_pda"], should_exist=False)

    return out


def case_resolve_branch_a_no_bets(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {"sigs": []}
    cfg = ctx.fetch_config()
    treasury_ata = _ata(cfg["treasury_wallet"], ctx.skr_mint)

    md = ctx.derive_next_match()
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_create_match(md, ctx.admin.pubkey())])
    out["sigs"].append(("create_match", sig))
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_lock_match(md, ctx.admin.pubkey())])
    out["sigs"].append(("lock_match", sig))
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_resolve_match(md, ctx.admin.pubkey(), SIDE_A, treasury_ata)])
    out["sigs"].append(("resolve_zero_pool", sig))

    ctx.wait_account(md["match_pda"], should_exist=False)
    ctx.wait_account(md["vault_ata"], should_exist=False)
    return out


def case_resolve_branch_b_winning_total_zero(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {"sigs": []}
    cfg = ctx.fetch_config()
    treasury_wallet: Pubkey = cfg["treasury_wallet"]
    treasury_ata = _ata(treasury_wallet, ctx.skr_mint)

    md = ctx.derive_next_match()
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_create_match(md, ctx.admin.pubkey())])
    out["sigs"].append(("create_match", sig))

    user1_bet_pda, _ = Pubkey.find_program_address([b"bet", bytes(md["match_pda"]), bytes(ctx.u1.pubkey())], ctx.program_id)
    sig = ctx.send_ok(ctx.u1, [ctx.u1], [ctx.ix_place_bet(md, ctx.u1.pubkey(), SIDE_A, 300)])
    out["sigs"].append(("place_bet_u1_A_300", sig))
    _expect(ctx.account_exists(user1_bet_pda), "user1 bet PDA missing before branch-B resolve")

    treasury_before = ctx.token_amount(treasury_ata)
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_lock_match(md, ctx.admin.pubkey())])
    out["sigs"].append(("lock_match", sig))
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_resolve_match(md, ctx.admin.pubkey(), SIDE_B, treasury_ata)])
    out["sigs"].append(("resolve_winner_B", sig))

    # Branch-B should close the vault immediately, keep match as RESOLVED for loser-bet cleanup.
    ctx.wait_account(md["vault_ata"], should_exist=False)
    m = ctx.fetch_match(md["match_pda"])
    _expect(m["status"] == STATUS_RESOLVED, "branch-B match status mismatch")
    _expect(m["winning_total"] == 0, "branch-B winning_total must be zero")

    treasury_after = ctx.token_amount(treasury_ata)
    _expect(treasury_after - treasury_before == 300, f"branch-B treasury sweep mismatch: {treasury_after - treasury_before} != 300")

    # Close the losing bet PDA; this should auto-close the match because all losers are cleaned.
    _expect(ctx.account_exists(user1_bet_pda), "branch-B loser bet PDA missing before close_losing_bet")
    sig = ctx.send_ok(
        payer=ctx.payer,
        signers=[ctx.payer],
        instructions=[ctx.ix_close_losing_bet(md, ctx.u1.pubkey(), ctx.admin.pubkey(), ctx.payer.pubkey())],
    )
    out["sigs"].append(("close_losing_bet_u1_branch_b", sig))

    ctx.wait_account(user1_bet_pda, should_exist=False)
    ctx.wait_account(md["match_pda"], should_exist=False)
    return out


def case_cancel_and_refund(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {"sigs": [], "fails": []}
    cfg = ctx.fetch_config()
    _expect(cfg["paused"] is False, "System paused unexpectedly")

    md = ctx.derive_next_match()
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_create_match(md, ctx.admin.pubkey())])
    out["sigs"].append(("create_match", sig))
    sig = ctx.send_ok(ctx.u1, [ctx.u1], [ctx.ix_place_bet(md, ctx.u1.pubkey(), SIDE_A, 150)])
    out["sigs"].append(("place_bet_u1_A_150", sig))
    sig = ctx.send_ok(ctx.u2, [ctx.u2], [ctx.ix_place_bet(md, ctx.u2.pubkey(), SIDE_B, 200)])
    out["sigs"].append(("place_bet_u2_B_200", sig))
    sig = ctx.send_ok(ctx.u3, [ctx.u3], [ctx.ix_place_bet(md, ctx.u3.pubkey(), SIDE_A, 100)])
    out["sigs"].append(("place_bet_u3_A_100", sig))

    # Cancel while OPEN.
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_cancel_match(md, ctx.admin.pubkey())])
    out["sigs"].append(("cancel_match", sig))
    _expect(ctx.fetch_match(md["match_pda"])["status"] == STATUS_CANCELLED, "match status not cancelled")

    # Cancel again should fail.
    r = ctx.send_fail(ctx.admin, [ctx.admin], [ctx.ix_cancel_match(md, ctx.admin.pubkey())])
    out["fails"].append(("cancel_twice", r))

    sig = ctx.send_ok(ctx.u1, [ctx.u1], [ctx.ix_refund_bet(md, ctx.u1.pubkey(), ctx.admin.pubkey())])
    out["sigs"].append(("refund_u1", sig))
    sig = ctx.send_ok(ctx.u2, [ctx.u2], [ctx.ix_refund_bet(md, ctx.u2.pubkey(), ctx.admin.pubkey())])
    out["sigs"].append(("refund_u2", sig))
    sig = ctx.send_ok(ctx.u3, [ctx.u3], [ctx.ix_refund_bet(md, ctx.u3.pubkey(), ctx.admin.pubkey())])
    out["sigs"].append(("refund_u3_final", sig))

    ctx.wait_account(md["match_pda"], should_exist=False)
    ctx.wait_account(md["vault_ata"], should_exist=False)

    # Refunding already-closed bet should fail.
    r = ctx.send_fail(ctx.u1, [ctx.u1], [ctx.ix_refund_bet(md, ctx.u1.pubkey(), ctx.admin.pubkey())])
    out["fails"].append(("refund_after_close", r))
    return out


def case_cancel_empty(ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {"sigs": []}
    md = ctx.derive_next_match()
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_create_match(md, ctx.admin.pubkey())])
    out["sigs"].append(("create_match", sig))
    sig = ctx.send_ok(ctx.admin, [ctx.admin], [ctx.ix_cancel_match(md, ctx.admin.pubkey())])
    out["sigs"].append(("cancel_empty_match", sig))
    ctx.wait_account(md["match_pda"], should_exist=False)
    return out


def run_case(name: str, fn, results: list[dict[str, Any]]) -> None:
    try:
        details = fn()
        print(f"[pass] {name}")
        results.append({"case": name, "status": "pass", "details": details})
    except Exception as exc:
        print(f"[fail] {name}: {exc}", file=sys.stderr)
        results.append(
            {
                "case": name,
                "status": "fail",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )


def main() -> int:
    rpc_url = os.getenv("SOLANA_URL", "https://api.devnet.solana.com")
    program_id = Pubkey.from_string(_required("BETTING_PROGRAM_ID"))
    skr_mint = Pubkey.from_string(_required("SKR_MINT"))
    deployer = _load_keypair(_required("DEPLOYER_KEYPAIR"))
    admin = _load_keypair(_required("ADMIN_KEYPAIR"))
    u1 = _load_keypair(_required("U1_KEYPAIR"))
    u2 = _load_keypair(_required("U2_KEYPAIR"))
    u3 = _load_keypair(_required("U3_KEYPAIR"))
    intruder = _load_keypair(_required("INTRUDER_KEYPAIR"))
    payer = _load_keypair(_required("PAYER_KEYPAIR"))
    config_pda, _ = Pubkey.find_program_address([b"config"], program_id)

    ctx = Ctx(
        rpc_url=rpc_url,
        program_id=program_id,
        skr_mint=skr_mint,
        deployer=deployer,
        admin=admin,
        u1=u1,
        u2=u2,
        u3=u3,
        intruder=intruder,
        payer=payer,
        config_pda=config_pda,
    )

    # Sanity checks.
    cfg = ctx.fetch_config()
    _expect(cfg["admin"] == admin.pubkey(), f"Admin mismatch: on-chain={cfg['admin']} local={admin.pubkey()}")
    _expect(cfg["skr_mint"] == skr_mint, "SKR mint mismatch with config")

    # Ensure user token accounts exist from shell funding.
    for u in [u1, u2, u3]:
        ata = _ata(u.pubkey(), skr_mint)
        _expect(ctx.account_exists(ata), f"user ATA missing: {ata}")

    results: list[dict[str, Any]] = []
    started = int(time.time())

    run_case("Config/Pause/Validation", lambda: case_config_pause_and_validation(ctx), results)
    run_case("HappyPath+Negatives", lambda: case_happy_path_and_negative_edges(ctx), results)
    run_case("ResolveBranchA_NoBets", lambda: case_resolve_branch_a_no_bets(ctx), results)
    run_case("ResolveBranchB_WinningTotalZero", lambda: case_resolve_branch_b_winning_total_zero(ctx), results)
    run_case("CancelAndRefund", lambda: case_cancel_and_refund(ctx), results)
    run_case("CancelEmptyImmediateClose", lambda: case_cancel_empty(ctx), results)

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = len(results) - passed
    summary = {
        "program_id": str(program_id),
        "config_pda": str(config_pda),
        "started_at_unix": started,
        "finished_at_unix": int(time.time()),
        "passed_cases": passed,
        "failed_cases": failed,
        "results": results,
    }

    print(json.dumps(summary, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
