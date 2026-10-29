from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PageParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    message: str
