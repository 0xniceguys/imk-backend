"""
Solana transaction builder + RPC helpers.

Uses `solders` for transaction construction and `httpx` for RPC calls.

Anchor Instruction Layout:
  [0:8]  — 8-byte discriminator = sha256("global:<ix_name>")[:8]
  [8:]   — Borsh-encoded arguments

PDA derivation uses SHA256 with canonical bump (find_program_address).
"""

import hashlib
import struct
import base64
from typing import Any

import httpx
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

MAINNET_RPC = "https://api.mainnet-beta.solana.com"
DEVNET_RPC  = "https://api.devnet.solana.com"

# ── Token mints ───────────────────────────────────────────────────────────────
SEEKER_MINT        = "SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3"
SEEKER_MINT_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"  # USDC on devnet standin

# ── Program IDs ───────────────────────────────────────────────────────────────
# Current devnet deployment.
BETTING_PROGRAM_ID = "CoTfhg7a9vjZMCCuvpxmnhSj9CzTAahxUvDutzZjRrth"

_TOKEN_PROGRAM_ID     = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
# Canonical SPL Associated Token Program ID expected by on-chain Anchor checks.
_ASSOC_TOKEN_PROG_ID  = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
_SYSTEM_PROGRAM_ID    = Pubkey.from_string("11111111111111111111111111111111")
_RENT_SYSVAR_ID       = Pubkey.from_string("SysvarRent111111111111111111111111111111111")

_RPC_HEADERS = {"Content-Type": "application/json"}


# ─────────────────────────────────────────────────────────────────────────────
# RPC helpers
# ─────────────────────────────────────────────────────────────────────────────

async def get_recent_blockhash(rpc_url: str) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getLatestBlockhash",
        "params": [{"commitment": "confirmed"}],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(rpc_url, headers=_RPC_HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()["result"]["value"]["blockhash"]


async def get_account_info(pubkey: str, rpc_url: str) -> dict[str, Any] | None:
    """Return raw getAccountInfo.value or None if account does not exist."""
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "getAccountInfo",
        "params": [pubkey, {"encoding": "base64", "commitment": "confirmed"}],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(rpc_url, headers=_RPC_HEADERS, json=payload)
    resp.raise_for_status()
    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"Solana RPC getAccountInfo error: {result['error']}")
    return result.get("result", {}).get("value")


async def account_exists(pubkey: str, rpc_url: str) -> bool:
    return await get_account_info(pubkey, rpc_url) is not None


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
        "admin": str(admin),
        "skr_mint": str(skr_mint),
        "treasury_wallet": str(treasury_wallet),
        "fee_bps": fee_bps,
        "min_bet": min_bet,
        "max_bet": max_bet,
        "match_counter": match_counter,
        "paused": paused,
    }


def _decode_match(raw: bytes) -> dict[str, Any]:
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
    vault_authority = str(Pubkey.from_bytes(raw[off : off + 32])); off += 32
    vault_ata = str(Pubkey.from_bytes(raw[off : off + 32])); off += 32
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


async def fetch_config(program_id_str: str, rpc_url: str) -> dict[str, Any]:
    program = Pubkey.from_string(program_id_str)
    config_pda = derive_config_pda(program)
    info = await get_account_info(str(config_pda), rpc_url)
    if info is None:
        raise RuntimeError(f"Config account not found: {config_pda}")
    raw = base64.b64decode(info["data"][0])
    decoded = _decode_config(raw)
    decoded["config_pda"] = str(config_pda)
    return decoded


async def fetch_match(match_pda_str: str, rpc_url: str) -> dict[str, Any] | None:
    info = await get_account_info(match_pda_str, rpc_url)
    if info is None:
        return None
    raw = base64.b64decode(info["data"][0])
    return _decode_match(raw)


async def get_token_account(owner: str, mint: str, rpc_url: str) -> str | None:
    """Return the first SPL token account pubkey for owner+mint, or None."""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "getTokenAccountsByOwner",
        "params": [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(rpc_url, headers=_RPC_HEADERS, json=payload)
        if resp.status_code == 200:
            accounts = resp.json().get("result", {}).get("value", [])
            if accounts:
                return accounts[0]["pubkey"]
    except Exception:
        pass
    return None


async def confirm_transaction(sig: str, rpc_url: str, retries: int = 10) -> bool:
    """Poll for transaction confirmation. Returns True when confirmed."""
    import asyncio
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getSignatureStatuses",
        "params": [[sig], {"searchTransactionHistory": True}],
    }
    for _ in range(retries):
        await asyncio.sleep(2)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(rpc_url, headers=_RPC_HEADERS, json=payload)
        if resp.status_code == 200:
            result = resp.json().get("result", {}).get("value", [None])
            status = result[0] if result else None
            if status and status.get("err") is not None:
                raise RuntimeError(f"Transaction {sig} failed on-chain: {status['err']}")
            if status and status.get("confirmationStatus") in ("confirmed", "finalized"):
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# PDA derivation
# ─────────────────────────────────────────────────────────────────────────────


def derive_config_pda(program_id: Pubkey) -> Pubkey:
    pk, _ = Pubkey.find_program_address([b"config"], program_id)
    return pk


def derive_match_pda(match_id: int, program_id: Pubkey) -> Pubkey:
    pk, _ = Pubkey.find_program_address(
        [b"match", struct.pack("<Q", match_id)],
        program_id,
    )
    return pk


def derive_vault_auth_pda(match_pda: Pubkey, program_id: Pubkey) -> Pubkey:
    pk, _ = Pubkey.find_program_address(
        [b"vault_auth", bytes(match_pda)],
        program_id,
    )
    return pk


def derive_user_bet_pda(match_pda: Pubkey, user: Pubkey, program_id: Pubkey) -> Pubkey:
    pk, _ = Pubkey.find_program_address(
        [b"bet", bytes(match_pda), bytes(user)],
        program_id,
    )
    return pk


def derive_associated_token_address(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive Associated Token Account address (ATA)."""
    pk, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(_TOKEN_PROGRAM_ID), bytes(mint)],
        _ASSOC_TOKEN_PROG_ID,
    )
    return pk


# ─────────────────────────────────────────────────────────────────────────────
# Anchor discriminator
# ─────────────────────────────────────────────────────────────────────────────

def _anchor_discriminator(ix_name: str) -> bytes:
    """Compute Anchor 8-byte instruction discriminator = sha256('global:<ix_name>')[:8]."""
    return hashlib.sha256(f"global:{ix_name}".encode()).digest()[:8]


# Pre-computed discriminators for our instructions
_DISC_PLACE_BET      = _anchor_discriminator("place_bet")
_DISC_CLAIM          = _anchor_discriminator("claim")
_DISC_REFUND_BET     = _anchor_discriminator("refund_bet")
_DISC_CREATE_MATCH   = _anchor_discriminator("create_match")
_DISC_LOCK_MATCH     = _anchor_discriminator("lock_match")
_DISC_RESOLVE_MATCH  = _anchor_discriminator("resolve_match")
_DISC_CLOSE_LOSING_BET = _anchor_discriminator("close_losing_bet")


# ─────────────────────────────────────────────────────────────────────────────
# Borsh serialization helpers
# ─────────────────────────────────────────────────────────────────────────────

def _encode_winner_side(side: str) -> bytes:
    """Encode WinnerSide enum: 'A'→1, 'B'→2."""
    mapping = {"A": 1, "B": 2, "NONE": 0}
    v = mapping.get(side.upper())
    if v is None:
        raise ValueError(f"Invalid side: {side!r}. Must be 'A' or 'B'")
    return bytes([v])


# ─────────────────────────────────────────────────────────────────────────────
# Instruction builders
# ─────────────────────────────────────────────────────────────────────────────

def _tx_bytes(ix: Instruction, fee_payer: Pubkey, blockhash: str) -> bytes:
    msg = Message.new_with_blockhash([ix], fee_payer, Hash.from_string(blockhash))
    tx = Transaction.new_unsigned(msg)
    return bytes(tx)


def build_place_bet_ix(
    user_pubkey: str,
    match_pda_str: str,
    skr_mint_str: str,
    side: str,            # "A" or "B"
    amount_base_units: int,
    blockhash: str,
    program_id_str: str = BETTING_PROGRAM_ID,
) -> bytes:
    """
    Build an unsigned `place_bet` transaction.

    Args:
        user_pubkey: Base58 public key of the bettor (Privy embedded wallet)
        match_pda_str: Base58 pubkey of the on-chain Match account
        skr_mint_str: Base58 pubkey of the SKR mint
        side: "A" (fighter1) or "B" (fighter2)
        amount_base_units: SKR amount in base units (1 SKR = 1_000_000)
        blockhash: Recent blockhash from getLatestBlockhash
        program_id_str: The deployed betting program ID
    Returns:
        Raw unsigned transaction bytes (base64-encode before sending to Privy)
    """
    prog   = Pubkey.from_string(program_id_str)
    user   = Pubkey.from_string(user_pubkey)
    match_pda  = Pubkey.from_string(match_pda_str)
    skr_mint   = Pubkey.from_string(skr_mint_str)

    config_pda   = derive_config_pda(prog)
    vault_auth   = derive_vault_auth_pda(match_pda, prog)
    vault_ata    = derive_associated_token_address(vault_auth, skr_mint)
    user_skr_ata = derive_associated_token_address(user, skr_mint)
    user_bet_pda = derive_user_bet_pda(match_pda, user, prog)

    # Borsh encode: side (u8 enum) + amount (u64 LE)
    data = _DISC_PLACE_BET + _encode_winner_side(side) + struct.pack("<Q", amount_base_units)

    ix = Instruction(
        program_id=prog,
        accounts=[
            AccountMeta(pubkey=config_pda,   is_signer=False, is_writable=False),
            AccountMeta(pubkey=match_pda,    is_signer=False, is_writable=True),
            AccountMeta(pubkey=user_bet_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=user_skr_ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=vault_ata,    is_signer=False, is_writable=True),
            AccountMeta(pubkey=vault_auth,   is_signer=False, is_writable=False),
            AccountMeta(pubkey=skr_mint,     is_signer=False, is_writable=False),
            AccountMeta(pubkey=user,         is_signer=True,  is_writable=True),
            AccountMeta(pubkey=_TOKEN_PROGRAM_ID,    is_signer=False, is_writable=False),
            AccountMeta(pubkey=_ASSOC_TOKEN_PROG_ID, is_signer=False, is_writable=False),
            AccountMeta(pubkey=_SYSTEM_PROGRAM_ID,   is_signer=False, is_writable=False),
        ],
        data=bytes(data),
    )
    return _tx_bytes(ix, user, blockhash)


def build_claim_ix(
    user_pubkey: str,
    match_pda_str: str,
    skr_mint_str: str,
    treasury_wallet_str: str,
    admin_pubkey_str: str,
    blockhash: str,
    program_id_str: str = BETTING_PROGRAM_ID,
) -> bytes:
    """
    Build an unsigned `claim` transaction (winner claims their SKR payout).
    """
    prog     = Pubkey.from_string(program_id_str)
    user     = Pubkey.from_string(user_pubkey)
    match_pda = Pubkey.from_string(match_pda_str)
    skr_mint  = Pubkey.from_string(skr_mint_str)
    treasury_wallet = Pubkey.from_string(treasury_wallet_str)
    admin    = Pubkey.from_string(admin_pubkey_str)

    config_pda    = derive_config_pda(prog)
    vault_auth    = derive_vault_auth_pda(match_pda, prog)
    vault_ata     = derive_associated_token_address(vault_auth, skr_mint)
    user_skr_ata  = derive_associated_token_address(user, skr_mint)
    user_bet_pda  = derive_user_bet_pda(match_pda, user, prog)
    treasury_ata  = derive_associated_token_address(treasury_wallet, skr_mint)

    data = bytes(_DISC_CLAIM)

    ix = Instruction(
        program_id=prog,
        accounts=[
            AccountMeta(pubkey=config_pda,   is_signer=False, is_writable=False),
            AccountMeta(pubkey=match_pda,    is_signer=False, is_writable=True),
            AccountMeta(pubkey=user_bet_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=user_skr_ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=vault_ata,    is_signer=False, is_writable=True),
            AccountMeta(pubkey=vault_auth,   is_signer=False, is_writable=True),
            AccountMeta(pubkey=treasury_ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=treasury_wallet, is_signer=False, is_writable=True),
            AccountMeta(pubkey=admin,        is_signer=False, is_writable=True),
            AccountMeta(pubkey=skr_mint,     is_signer=False, is_writable=False),
            AccountMeta(pubkey=user,         is_signer=True,  is_writable=True),
            AccountMeta(pubkey=_TOKEN_PROGRAM_ID,  is_signer=False, is_writable=False),
            AccountMeta(pubkey=_SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
        data=bytes(data),
    )
    return _tx_bytes(ix, user, blockhash)


def build_create_match_ix(
    admin_keypair: Keypair,
    skr_mint_str: str,
    match_counter: int,
    model_a_hash: bytes,
    model_b_hash: bytes,
    blockhash: str,
    program_id_str: str = BETTING_PROGRAM_ID,
) -> Transaction:
    """
    Build a signed `create_match` transaction (admin-only).
    Returns a signed Transaction ready to broadcast.
    """
    prog     = Pubkey.from_string(program_id_str)
    admin    = admin_keypair.pubkey()
    skr_mint = Pubkey.from_string(skr_mint_str)

    config_pda  = derive_config_pda(prog)
    match_pda   = derive_match_pda(match_counter, prog)
    vault_auth  = derive_vault_auth_pda(match_pda, prog)
    vault_ata   = derive_associated_token_address(vault_auth, skr_mint)

    assert len(model_a_hash) == 32
    assert len(model_b_hash) == 32
    data = bytes(_DISC_CREATE_MATCH) + bytes(model_a_hash) + bytes(model_b_hash)

    ix = Instruction(
        program_id=prog,
        accounts=[
            AccountMeta(pubkey=config_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=match_pda,  is_signer=False, is_writable=True),
            AccountMeta(pubkey=vault_auth, is_signer=False, is_writable=False),
            AccountMeta(pubkey=vault_ata,  is_signer=False, is_writable=True),
            AccountMeta(pubkey=skr_mint,   is_signer=False, is_writable=False),
            AccountMeta(pubkey=admin,      is_signer=True,  is_writable=True),
            AccountMeta(pubkey=_TOKEN_PROGRAM_ID,    is_signer=False, is_writable=False),
            AccountMeta(pubkey=_ASSOC_TOKEN_PROG_ID, is_signer=False, is_writable=False),
            AccountMeta(pubkey=_SYSTEM_PROGRAM_ID,   is_signer=False, is_writable=False),
            AccountMeta(pubkey=_RENT_SYSVAR_ID,      is_signer=False, is_writable=False),
        ],
        data=bytes(data),
    )
    # solders Transaction constructor: Transaction(signers, message, blockhash)
    msg = Message.new_with_blockhash([ix], admin, Hash.from_string(blockhash))
    tx = Transaction([admin_keypair], msg, Hash.from_string(blockhash))
    return tx


def build_lock_match_ix(
    admin_keypair: Keypair,
    match_pda_str: str,
    blockhash: str,
    program_id_str: str = BETTING_PROGRAM_ID,
) -> Transaction:
    """Build a signed `lock_match` transaction (admin-only)."""
    prog      = Pubkey.from_string(program_id_str)
    admin     = admin_keypair.pubkey()
    match_pda = Pubkey.from_string(match_pda_str)
    config_pda = derive_config_pda(prog)

    data = bytes(_DISC_LOCK_MATCH)

    ix = Instruction(
        program_id=prog,
        accounts=[
            AccountMeta(pubkey=config_pda, is_signer=False, is_writable=False),
            AccountMeta(pubkey=match_pda,  is_signer=False, is_writable=True),
            AccountMeta(pubkey=admin,      is_signer=True,  is_writable=False),
        ],
        data=bytes(data),
    )
    msg = Message.new_with_blockhash([ix], admin, Hash.from_string(blockhash))
    tx = Transaction([admin_keypair], msg, Hash.from_string(blockhash))
    return tx


def build_resolve_match_ix(
    admin_keypair: Keypair,
    match_pda_str: str,
    skr_mint_str: str,
    treasury_wallet_str: str,
    winner_side: str,   # "A" or "B"
    blockhash: str,
    program_id_str: str = BETTING_PROGRAM_ID,
) -> Transaction:
    """Build a signed `resolve_match` transaction (admin-only)."""
    prog      = Pubkey.from_string(program_id_str)
    admin     = admin_keypair.pubkey()
    match_pda = Pubkey.from_string(match_pda_str)
    skr_mint  = Pubkey.from_string(skr_mint_str)
    treasury_wallet = Pubkey.from_string(treasury_wallet_str)

    config_pda   = derive_config_pda(prog)
    vault_auth   = derive_vault_auth_pda(match_pda, prog)
    vault_ata    = derive_associated_token_address(vault_auth, skr_mint)
    treasury_ata = derive_associated_token_address(treasury_wallet, skr_mint)

    data = bytes(_DISC_RESOLVE_MATCH) + _encode_winner_side(winner_side)

    ix = Instruction(
        program_id=prog,
        accounts=[
            AccountMeta(pubkey=config_pda,    is_signer=False, is_writable=False),
            AccountMeta(pubkey=match_pda,     is_signer=False, is_writable=True),
            AccountMeta(pubkey=vault_ata,     is_signer=False, is_writable=True),
            AccountMeta(pubkey=vault_auth,    is_signer=False, is_writable=True),
            AccountMeta(pubkey=treasury_ata,  is_signer=False, is_writable=True),
            AccountMeta(pubkey=skr_mint,      is_signer=False, is_writable=False),
            AccountMeta(pubkey=admin,         is_signer=True,  is_writable=True),
            AccountMeta(pubkey=_TOKEN_PROGRAM_ID,  is_signer=False, is_writable=False),
            AccountMeta(pubkey=_SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
        data=bytes(data),
    )
    msg = Message.new_with_blockhash([ix], admin, Hash.from_string(blockhash))
    tx = Transaction([admin_keypair], msg, Hash.from_string(blockhash))
    return tx


def build_close_losing_bet_ix(
    payer_keypair: Keypair,
    match_pda_str: str,
    losing_user_pubkey_str: str,
    admin_pubkey_str: str,
    blockhash: str,
    program_id_str: str = BETTING_PROGRAM_ID,
) -> Transaction:
    """
    Build a signed `close_losing_bet` transaction.

    This is permissionless on-chain; payer can be any signer.
    `admin_pubkey_str` must match config.admin because rent is sent there.
    """
    prog = Pubkey.from_string(program_id_str)
    payer = payer_keypair.pubkey()
    match_pda = Pubkey.from_string(match_pda_str)
    losing_user = Pubkey.from_string(losing_user_pubkey_str)
    admin = Pubkey.from_string(admin_pubkey_str)

    config_pda = derive_config_pda(prog)
    user_bet_pda = derive_user_bet_pda(match_pda, losing_user, prog)

    ix = Instruction(
        program_id=prog,
        accounts=[
            AccountMeta(pubkey=config_pda, is_signer=False, is_writable=False),
            AccountMeta(pubkey=match_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=user_bet_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=admin, is_signer=False, is_writable=True),
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
        ],
        data=bytes(_DISC_CLOSE_LOSING_BET),
    )
    msg = Message.new_with_blockhash([ix], payer, Hash.from_string(blockhash))
    tx = Transaction([payer_keypair], msg, Hash.from_string(blockhash))
    return tx


async def send_transaction(tx: Transaction, rpc_url: str) -> str:
    """Broadcast a signed transaction. Returns the transaction signature."""
    tx_b64 = base64.b64encode(bytes(tx)).decode()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [tx_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(rpc_url, headers=_RPC_HEADERS, json=payload)
    resp.raise_for_status()
    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"Solana RPC sendTransaction error: {result['error']}")
    return result["result"]


async def send_and_confirm_transaction(tx: Transaction, rpc_url: str, retries: int = 15) -> str:
    """Broadcast and wait for confirmation; raises on failure/timeout."""
    sig = await send_transaction(tx, rpc_url)
    confirmed = await confirm_transaction(sig, rpc_url, retries=retries)
    if not confirmed:
        raise RuntimeError(f"Transaction {sig} was not confirmed in time")
    return sig


# ─────────────────────────────────────────────────────────────────────────────
# Legacy helpers (kept for backwards compat)
# ─────────────────────────────────────────────────────────────────────────────

def build_sol_transfer(
    from_addr: str,
    to_addr: str,
    lamports: int,
    blockhash: str,
) -> bytes:
    """Build an unsigned SOL transfer transaction. Returns raw bytes."""
    from_pk = Pubkey.from_string(from_addr)
    to_pk   = Pubkey.from_string(to_addr)
    ix  = transfer(TransferParams(from_pubkey=from_pk, to_pubkey=to_pk, lamports=lamports))
    return _tx_bytes(ix, from_pk, blockhash)


def build_spl_transfer(
    owner: str,
    src_ata: str,
    dst_ata: str,
    amount: int,
    blockhash: str,
) -> bytes:
    """Build an unsigned SPL Token.transfer transaction. Returns raw bytes."""
    owner_pk = Pubkey.from_string(owner)
    src_pk   = Pubkey.from_string(src_ata)
    dst_pk   = Pubkey.from_string(dst_ata)

    data = bytes([3]) + amount.to_bytes(8, "little")
    ix = Instruction(
        program_id=_TOKEN_PROGRAM_ID,
        accounts=[
            AccountMeta(pubkey=src_pk,   is_signer=False, is_writable=True),
            AccountMeta(pubkey=dst_pk,   is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner_pk, is_signer=True,  is_writable=False),
        ],
        data=data,
    )
    return _tx_bytes(ix, owner_pk, blockhash)
