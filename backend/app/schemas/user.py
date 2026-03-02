from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class UserOut(BaseModel):
    id: UUID
    privy_user_id: str
    wallet_address: str | None = None
    email: str | None = None
    display_name: str | None = None
    is_admin: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    token: str
    walletAddress: str | None = None
