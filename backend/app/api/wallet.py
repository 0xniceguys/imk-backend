import base64
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.dependencies import get_current_user, get_db
from app.services.privy_wallet import get_wallet_id_and_sign
from app.services.solana_tx import (
    DEVNET_RPC,
    MAINNET_RPC,
    SEEKER_MINT,
    SEEKER_MINT_DEVNET,
    build_sol_transfer,
    build_spl_transfer,
    get_recent_blockhash,
    get_token_account,
)

router = APIRouter(prefix="/wallet", tags=["wallet"])


class WithdrawRequest(BaseModel):
    token: Literal["sol", "seeker"]
    to_address: str
    amount: float


class WithdrawResponse(BaseModel):
    tx_signature: str


class PrepareWithdrawRequest(BaseModel):
    token: Literal["sol", "seeker"]
    to_address: str
    amount: float


class PrepareWithdrawResponse(BaseModel):
    transaction_base64: str
    message: str


@router.post("/withdraw", response_model=WithdrawResponse)
async def withdraw(
    body: WithdrawRequest,
    authorization: str = Header(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    if not user.wallet_address:
        raise HTTPException(400, "No wallet address on file — please log in again")

    # Extract raw JWT for Privy user-delegated signing
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Authorization header")
    raw_jwt = authorization.split(" ", 1)[1]

    rpc = DEVNET_RPC if settings.use_devnet else MAINNET_RPC
    mint = SEEKER_MINT_DEVNET if settings.use_devnet else SEEKER_MINT

    # Fetch recent blockhash from Solana RPC
    blockhash = await get_recent_blockhash(rpc)

    # Build unsigned transaction
    if body.token == "sol":
        lamports = int(body.amount * 1_000_000_000)
        tx_bytes = build_sol_transfer(
            from_addr=user.wallet_address,
            to_addr=body.to_address,
            lamports=lamports,
            blockhash=blockhash,
        )
    else:
        src_ata = await get_token_account(user.wallet_address, mint, rpc)
        dst_ata = await get_token_account(body.to_address, mint, rpc)

        if src_ata is None:
            raise HTTPException(400, "No SEEKER token account found in your wallet")
        if dst_ata is None:
            raise HTTPException(
                400,
                "Recipient has no SEEKER token account — they need to receive SEEKER first",
            )

        token_amount = int(body.amount * 1_000_000_000)  # 9 decimals
        tx_bytes = build_spl_transfer(
            owner=user.wallet_address,
            src_ata=src_ata,
            dst_ata=dst_ata,
            amount=token_amount,
            blockhash=blockhash,
        )

    # Sign + broadcast via Privy (user-delegated via JWT)
    tx_b64 = base64.b64encode(tx_bytes).decode()
    tx_sig, corrected_address = await get_wallet_id_and_sign(
        raw_jwt, user.wallet_address, tx_b64, devnet=settings.use_devnet
    )

    # If Privy returned a different wallet address, update the database
    if corrected_address and corrected_address != user.wallet_address:
        print(f"Updating user wallet address from {user.wallet_address} to {corrected_address}")
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(wallet_address=corrected_address)
        )
        await db.commit()

    return WithdrawResponse(tx_signature=tx_sig)


@router.post("/withdraw/prepare", response_model=PrepareWithdrawResponse)
async def prepare_withdraw(
    body: PrepareWithdrawRequest,
    user: User = Depends(get_current_user),
):
    """
    Prepare an unsigned withdrawal transaction for client-side signing.
    Flutter will sign this with Privy's embedded wallet, then call POST /withdraw/broadcast.
    """
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    if not user.wallet_address:
        raise HTTPException(400, "No wallet address on file — please log in again")

    rpc = DEVNET_RPC if settings.use_devnet else MAINNET_RPC
    mint = SEEKER_MINT_DEVNET if settings.use_devnet else SEEKER_MINT

    # Fetch recent blockhash from Solana RPC
    blockhash = await get_recent_blockhash(rpc)

    # Build unsigned transaction
    if body.token == "sol":
        lamports = int(body.amount * 1_000_000_000)
        tx_bytes = build_sol_transfer(
            from_addr=user.wallet_address,
            to_addr=body.to_address,
            lamports=lamports,
            blockhash=blockhash,
        )
    else:
        src_ata = await get_token_account(user.wallet_address, mint, rpc)
        dst_ata = await get_token_account(body.to_address, mint, rpc)

        if src_ata is None:
            raise HTTPException(400, "No SEEKER token account found in your wallet")
        if dst_ata is None:
            raise HTTPException(
                400,
                "Recipient has no SEEKER token account — they need to receive SEEKER first",
            )

        token_amount = int(body.amount * 1_000_000_000)  # 9 decimals
        tx_bytes = build_spl_transfer(
            owner=user.wallet_address,
            src_ata=src_ata,
            dst_ata=dst_ata,
            amount=token_amount,
            blockhash=blockhash,
        )

    # Return unsigned transaction as base64
    tx_b64 = base64.b64encode(tx_bytes).decode()
    return PrepareWithdrawResponse(
        transaction_base64=tx_b64,
        message=f"Sign this transaction to withdraw {body.amount} {body.token.upper()} to {body.to_address}",
    )


class BroadcastWithdrawRequest(BaseModel):
    signed_transaction_base64: str


class BroadcastWithdrawResponse(BaseModel):
    tx_signature: str


@router.post("/withdraw/broadcast", response_model=BroadcastWithdrawResponse)
async def broadcast_withdraw(
    body: BroadcastWithdrawRequest,
    user: User = Depends(get_current_user),
):
    """
    Broadcast a signed transaction to the Solana network.
    The transaction must be signed by the user's wallet in Flutter.
    """
    import httpx

    rpc = DEVNET_RPC if settings.use_devnet else MAINNET_RPC

    # Broadcast the signed transaction to Solana
    async with httpx.AsyncClient() as client:
        response = await client.post(
            rpc,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    body.signed_transaction_base64,
                    {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"},
                ],
            },
            timeout=30.0,
        )

        if response.status_code != 200:
            raise HTTPException(502, f"Solana RPC error: {response.text}")

        result = response.json()

        if "error" in result:
            raise HTTPException(400, f"Transaction rejected: {result['error']}")

        if "result" not in result:
            raise HTTPException(502, f"Unexpected RPC response: {result}")

        tx_signature = result["result"]
        return BroadcastWithdrawResponse(tx_signature=tx_signature)
