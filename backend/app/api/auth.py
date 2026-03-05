from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.db.models import User
from app.schemas.user import LoginRequest, UserOut, UserUpdate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify Privy JWT and return user profile.
    Auto-creates user on first login.
    """
    from app.auth.privy import verify_privy_token
    from sqlalchemy import select

    try:
        claims = await verify_privy_token(body.token)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(401, str(e))

    privy_user_id = claims.get("sub")
    if not privy_user_id:
        from fastapi import HTTPException
        raise HTTPException(401, "Token missing sub claim")

    # ✅ FIX: Use upsert pattern to handle concurrent first-login race condition
    # Try to get existing user first
    result = await db.execute(
        select(User).where(User.privy_user_id == privy_user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Create new user with data from request body (not JWT claims)
        try:
            user = User(
                privy_user_id=privy_user_id,
                wallet_address=body.walletAddress,
                email=body.email,
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
    else:
        # Update existing user if wallet/email changed
        changed = False
        if body.walletAddress and user.wallet_address != body.walletAddress:
            user.wallet_address = body.walletAddress
            changed = True
        if body.email and user.email != body.email:
            user.email = body.email
            changed = True
        if changed:
            await db.commit()
            await db.refresh(user)

    return user


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update mutable user profile fields (display_name)."""
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or None
    await db.commit()
    await db.refresh(user)
    return user
