import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class JobCreate(BaseModel):
    job_type: str = Field(examples=["diagnostic"])
    project_id: uuid.UUID | None = None
    params: dict = Field(default_factory=dict)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    job_type: str
    status: str
    params: dict
    steps: list[dict]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class UsageOut(BaseModel):
    sku: str
    day_count: int
    daily_limit: int = Field(description="0 = no daily cap")
    month_count: int
    monthly_limit: int
    remaining: int
    label: str | None = None
    blocked: str | None = Field(None, description="Why it cannot be used right now, with the reset time")
