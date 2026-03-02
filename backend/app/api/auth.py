from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.db.models import User
from app.schemas.user import LoginRequest, UserOut

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

    result = await db.execute(
        select(User).where(User.privy_user_id == privy_user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            privy_user_id=privy_user_id,
            wallet_address=claims.get("wallet", {}).get("address"),
            email=claims.get("email", {}).get("address"),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
