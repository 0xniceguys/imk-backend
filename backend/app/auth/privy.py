import time

import httpx
from jose import jwt, JWTError

from app.config import settings

_jwks_cache: dict | None = None
_jwks_fetched_at: float = 0
_JWKS_TTL = 3600  # Re-fetch JWKS every hour


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    now = time.time()
    if _jwks_cache is not None and (now - _jwks_fetched_at) < _JWKS_TTL:
        return _jwks_cache

    url = f"https://auth.privy.io/api/v1/apps/{settings.privy_app_id}/jwks"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now
        return _jwks_cache


def _find_key(jwks: dict, kid: str) -> dict | None:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


async def verify_privy_token(token: str) -> dict:
    """
    Verify a Privy access token and return decoded claims.
    Raises ValueError on invalid/expired token.
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise ValueError(f"Invalid token header: {e}")

    kid = header.get("kid")
    if not kid:
        raise ValueError("Token missing kid header")

    jwks = await _get_jwks()
    key = _find_key(jwks, kid)
    if key is None:
        # Force refresh in case keys rotated
        global _jwks_fetched_at
        _jwks_fetched_at = 0
        jwks = await _get_jwks()
        key = _find_key(jwks, kid)
        if key is None:
            raise ValueError(f"No matching key for kid={kid}")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["ES256"],
            audience=settings.privy_app_id,
            issuer="privy.io",
        )
        return claims
    except JWTError as e:
        raise ValueError(f"Token verification failed: {e}")
