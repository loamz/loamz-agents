from pydantic import BaseModel, Field


class FeedIn(BaseModel):
    since: dict[str, int] = Field(default_factory=dict, max_length=1000)
    watch: str | None = Field(default=None, max_length=256)
