import os

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.privy import verify_privy_token
from app.db.engine import async_session
from app.db.models import User


def _extract_bearer_subject(authorization: str | None) -> str:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            return token
    return "dev-test-user"


async def get_db():
    async with async_session() as session:
        yield session


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    # DEV BYPASS: DEV_USER_BYPASS=true skips Privy for local testing.
    if os.getenv("DEV_USER_BYPASS", "").lower() == "true":
        from app.config import settings
        from app.services.dev_local_signer import wallet_address_for_subject

        subject = _extract_bearer_subject(authorization)
        result = await db.execute(
            select(User).where(User.privy_user_id == subject)
        )
        user = result.scalar_one_or_none()

        wallet_address = None
        if settings.dev_local_signer_bypass:
            try:
                wallet_address = wallet_address_for_subject(subject)
            except ValueError as exc:
                raise HTTPException(400, str(exc))

        if user is None:
            user = User(
                privy_user_id=subject,
                wallet_address=wallet_address,
                display_name=f"Dev User {subject}",
                is_admin=False,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        elif wallet_address and user.wallet_address != wallet_address:
            user.wallet_address = wallet_address
            await db.commit()
            await db.refresh(user)
        return user

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    token = authorization.split(" ", 1)[1]
    try:
        claims = await verify_privy_token(token)
    except ValueError as e:
        raise HTTPException(401, str(e))

    privy_user_id = claims.get("sub")
    if not privy_user_id:
        raise HTTPException(401, "Token missing sub claim")

    result = await db.execute(
        select(User).where(User.privy_user_id == privy_user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        # ✅ FIX: Use upsert pattern to handle concurrent first-login race condition
        # Auto-create user on first authenticated request with retry on conflict
        try:
            user = User(
                privy_user_id=privy_user_id,
                wallet_address=claims.get("wallet", {}).get("address"),
                email=claims.get("email", {}).get("address"),
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        except Exception:
            # If unique constraint violation, another request created the user
            # Roll back and try to fetch again
            await db.rollback()
            result = await db.execute(
                select(User).where(User.privy_user_id == privy_user_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                # Still doesn't exist, re-raise original error
                raise

    return user


async def require_admin(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    # DEV BYPASS: DEV_ADMIN_BYPASS=true skips Privy for local testing.
    # Never enable this in production.
    if os.getenv("DEV_ADMIN_BYPASS", "").lower() == "true":
        # Return (or create) a synthetic dev admin user
        result = await db.execute(
            select(User).where(User.privy_user_id == "dev-admin-bypass")
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                privy_user_id="dev-admin-bypass",
                wallet_address="DevAdminBypassWallet",
                display_name="Dev Admin",
                is_admin=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        if not user.is_admin:
            user.is_admin = True
            await db.commit()
        return user

    # Production path: require valid Privy JWT with is_admin=True
    user = await get_current_user(authorization=authorization, db=db)
    if not user.is_admin:
        raise HTTPException(403, "Admin access required")
    return user
