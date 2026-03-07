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


async def get_wallet_id_and_sign(
    user_jwt: str, wallet_address: str, tx_b64: str, devnet: bool = False
) -> tuple[str, str | None]:
    """
    Locate user's Solana wallet and sign + broadcast a transaction via Privy's
    server RPC using user-delegated JWT authentication.

    Args:
        user_jwt: The user's JWT access token from Privy
        wallet_address: The user's wallet address stored in DB
        tx_b64: Base64-encoded transaction to sign
        devnet: Whether to use devnet or mainnet

    Returns:
        tuple: (transaction_signature, corrected_wallet_address_or_none)
    """
    caip2 = _CAIP2_DEVNET if devnet else _CAIP2_MAINNET

    def _sync() -> tuple[str, str | None]:
        from jose import jwt as jose_jwt
        import time

        # Decode and inspect JWT
        try:
            claims = jose_jwt.get_unverified_claims(user_jwt)
            privy_did = claims.get("sub")
            exp = claims.get("exp")
            iat = claims.get("iat")
            aud = claims.get("aud")

            print(f"DEBUG JWT: sub={privy_did}, aud={aud}, exp={exp}, iat={iat}, now={int(time.time())}")

            if exp and int(time.time()) >= exp:
                raise ValueError(f"JWT expired at {exp}, current time {int(time.time())}")

            if aud != settings.privy_app_id:
                raise ValueError(f"JWT audience mismatch: expected {settings.privy_app_id}, got {aud}")

        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to decode JWT: {e}")

        client = PrivyAPI(
            app_id=settings.privy_app_id,
            app_secret=settings.privy_app_secret,
        )

        # Authenticate with JWT and get user signer (this returns wallets + auth key)
        print(f"DEBUG: Calling generate_user_signer for user {privy_did}")
        print(f"DEBUG: JWT first 50 chars: {user_jwt[:50]}...")
        print(f"DEBUG: JWT last 20 chars: ...{user_jwt[-20:]}")

        try:
            signer_resp = client.wallets.generate_user_signer(user_jwt=user_jwt)
            print(f"DEBUG: Successfully authenticated! Got {len(signer_resp.wallets)} wallet(s)")
        except Exception as e:
            error_str = str(e)
            print(f"ERROR: generate_user_signer failed: {error_str}")

            # Check if it's the "Invalid JWT" error
            if "Invalid JWT" in error_str or "invalid_data" in error_str:
                raise ValueError(
                    f"Privy rejected the provided JWT for user signer authentication. "
                    f"Original error: {error_str}"
                )
            raise ValueError(f"Failed to authenticate with Privy: {error_str}")

        # Set the authorization key to allow wallet operations
        client.authorization_key = signer_resp.decrypted_authorization_key
        print(f"DEBUG: Authorization key set successfully")

        # Find the Privy embedded Solana wallet from the returned wallets
        wallet_id: str | None = None
        wallet_found_address: str | None = None

        for wallet in signer_resp.wallets:
            chain = getattr(wallet, "chain_type", None)
            addr = getattr(wallet, "address", None)
            wid = getattr(wallet, "id", None)
            wallet_client_type = getattr(wallet, "wallet_client_type", "unknown")

            print(f"DEBUG Wallet: chain={chain}, addr={addr}, id={wid}, client_type={wallet_client_type}")

            # Find Solana embedded wallet (Privy-managed, not imported external wallet)
            if chain == "solana" and wid:
                wallet_id = wid
                wallet_found_address = addr
                print(f"DEBUG: Selected Solana wallet - addr={addr}, id={wid}")
                break

        if not wallet_id:
            raise ValueError(
                f"No Privy embedded Solana wallet found. "
                f"User has {len(signer_resp.wallets)} wallet(s). "
                f"Only Privy embedded wallets can be used for server-side signing."
            )

        # Warn if addresses don't match
        if wallet_found_address != wallet_address:
            print(f"WARNING: DB wallet address mismatch! DB={wallet_address}, Privy={wallet_found_address}")

        # Sign and broadcast the transaction
        print(f"DEBUG: Calling wallets.rpc to sign and send transaction")
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

        print(f"DEBUG: Transaction signed and broadcast! Hash: {result.data.hash}")

        # Return tx signature and corrected address if there was a mismatch
        corrected_address = wallet_found_address if wallet_found_address != wallet_address else None
        return result.data.hash, corrected_address

    try:
        return await run_in_threadpool(_sync)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Privy wallet operation failed: {e}")
