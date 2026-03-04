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
# Update this to the deployed program ID when deploying to devnet/mainnet.
# Current value is the localnet placeholder from Anchor.toml.
BETTING_PROGRAM_ID = "7woZnJL2FL4yG44EEDgVtY3YX6TqGFF1yuWND4tiDuAv"

_TOKEN_PROGRAM_ID     = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
_ASSOC_TOKEN_PROG_ID  = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bxe")
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
            AccountMeta(pubkey=treasury_wallet, is_signer=False, is_writable=False),
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
