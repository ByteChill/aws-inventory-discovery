"""The paging parameters of the list endpoints."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class PagingParams(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
