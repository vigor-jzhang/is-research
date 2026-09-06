"""Common types."""

from pydantic import BaseModel


class Usage(BaseModel):
    model_config = {"extra": "forbid"}
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None
