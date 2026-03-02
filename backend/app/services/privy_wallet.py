"""
Privy server-side wallet operations using the official privy-client SDK.

Flow for signAndSendTransaction:
  1. generate_user_signer(user_jwt) — ephemeral HPKE key exchange — returns
     decrypted_authorization_key + the user's wallet list
  2. Set client.authorization_key = decrypted_authorization_key
  3. Find the Solana wallet_id from the returned wallets
  4. wallets.rpc(wallet_id, method="signAndSendTransaction", params, caip2)
  5. Return result.data.hash (tx signature)

The SDK is synchronous; calls are run in a thread pool to avoid blocking
FastAPI's async event loop.
"""

from fastapi import HTTPException
from privy import PrivyAPI
from starlette.concurrency import run_in_threadpool

from app.config import settings

_CAIP2_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
_CAIP2_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


async def get_wallet_id_and_sign(user_jwt: str, tx_b64: str, devnet: bool = False) -> str:
    """
    Authenticate as the user, locate their Solana wallet, then sign + broadcast
    a base64-encoded transaction via Privy's server RPC.

    Returns the transaction signature string.
    """
    caip2 = _CAIP2_DEVNET if devnet else _CAIP2_MAINNET

    def _sync() -> str:
        client = PrivyAPI(
            app_id=settings.privy_app_id,
            app_secret=settings.privy_app_secret,
        )

        # Authenticate as the user — ephemeral HPKE key exchange
        signer_resp = client.wallets.generate_user_signer(user_jwt=user_jwt)

        # Elevate client to act on behalf of this user
        client.authorization_key = signer_resp.decrypted_authorization_key

        # Find the Solana wallet from the returned wallet list
        wallet_id: str | None = None
        for w in signer_resp.wallets:
            if getattr(w, "chain_type", None) == "solana":
                wallet_id = w.id
                break

        if not wallet_id:
            raise ValueError("No Solana wallet found for this user")

        # Sign and broadcast
        result = client.wallets.rpc(
            wallet_id,
            method="signAndSendTransaction",
            params={"encoding": "base64", "transaction": tx_b64},
            caip2=caip2,
        )

        if result.error:
            raise ValueError(f"Privy RPC error: {result.error}")

        if not result.data or not result.data.hash:
            raise ValueError(f"Privy returned no transaction hash: {result}")

        return result.data.hash

    try:
        return await run_in_threadpool(_sync)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Privy sign+send failed: {e}")
