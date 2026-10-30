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


# Instrument symbols in path parameters: letters, digits and the characters NSE uses in
# trading symbols (e.g. "M&M", "BAJAJ-AUTO"). Rejects "..", "/", spaces and other input
# that is never a valid symbol, before it reaches a provider or a query (422).
SYMBOL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9&_\-]{0,31}$"
