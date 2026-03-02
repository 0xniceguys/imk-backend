"""
Solana transaction builder + RPC helpers.

Uses `solders` for transaction construction and `httpx` for RPC calls.
"""

import httpx
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

MAINNET_RPC = "https://api.mainnet-beta.solana.com"
DEVNET_RPC = "https://api.devnet.solana.com"

SEEKER_MINT = "SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3"
SEEKER_MINT_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"  # USDC on devnet

_TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

_RPC_HEADERS = {"Content-Type": "application/json"}


async def get_recent_blockhash(rpc_url: str) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getLatestBlockhash",
        "params": [{"commitment": "confirmed"}],
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(rpc_url, headers=_RPC_HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()["result"]["value"]["blockhash"]


async def get_token_account(owner: str, mint: str, rpc_url: str) -> str | None:
    """Return the first SPL token account pubkey for owner+mint, or None."""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "getTokenAccountsByOwner",
        "params": [
            owner,
            {"mint": mint},
            {"encoding": "jsonParsed"},
        ],
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(rpc_url, headers=_RPC_HEADERS, json=payload)
        if resp.status_code == 200:
            accounts = resp.json().get("result", {}).get("value", [])
            if accounts:
                return accounts[0]["pubkey"]
    except Exception:
        pass
    return None


def build_sol_transfer(
    from_addr: str,
    to_addr: str,
    lamports: int,
    blockhash: str,
) -> bytes:
    """Build an unsigned SOL transfer transaction. Returns raw bytes."""
    from_pk = Pubkey.from_string(from_addr)
    to_pk = Pubkey.from_string(to_addr)
    ix = transfer(TransferParams(from_pubkey=from_pk, to_pubkey=to_pk, lamports=lamports))
    msg = Message.new_with_blockhash([ix], from_pk, Hash.from_string(blockhash))
    tx = Transaction.new_unsigned(msg)
    return bytes(tx)


def build_spl_transfer(
    owner: str,
    src_ata: str,
    dst_ata: str,
    amount: int,
    blockhash: str,
) -> bytes:
    """Build an unsigned SPL Token.transfer transaction. Returns raw bytes."""
    owner_pk = Pubkey.from_string(owner)
    src_pk = Pubkey.from_string(src_ata)
    dst_pk = Pubkey.from_string(dst_ata)

    # SPL Token instruction: Transfer (discriminator = 3)
    # Data: [3] + [amount as u64 LE] = 9 bytes
    data = bytes([3]) + amount.to_bytes(8, "little")

    ix = Instruction(
        program_id=_TOKEN_PROGRAM_ID,
        accounts=[
            AccountMeta(pubkey=src_pk, is_signer=False, is_writable=True),
            AccountMeta(pubkey=dst_pk, is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner_pk, is_signer=True, is_writable=False),
        ],
        data=data,
    )

    msg = Message.new_with_blockhash([ix], owner_pk, Hash.from_string(blockhash))
    tx = Transaction.new_unsigned(msg)
    return bytes(tx)
