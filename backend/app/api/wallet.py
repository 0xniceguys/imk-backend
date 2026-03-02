import base64
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.db.models import User
from app.dependencies import get_current_user
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


@router.post("/withdraw", response_model=WithdrawResponse)
async def withdraw(
    body: WithdrawRequest,
    authorization: str | None = Header(None),
    user: User = Depends(get_current_user),
):
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    if not user.wallet_address:
        raise HTTPException(400, "No wallet address on file — please log in again")

    # Extract raw JWT to pass to Privy generate_user_signer
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
    tx_sig = await get_wallet_id_and_sign(raw_jwt, tx_b64, devnet=settings.use_devnet)

    return WithdrawResponse(tx_signature=tx_sig)
