from uuid import UUID

from pydantic import BaseModel


class StreamOut(BaseModel):
    match_id: UUID
    status: str
    hls_url: str | None = None
    vod_url: str | None = None
    viewer_count: int = 0

    model_config = {"from_attributes": True}
