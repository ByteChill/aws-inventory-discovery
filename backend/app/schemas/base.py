from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base of every wire model: camelCase in JSON, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Page[T](ApiModel):
    items: list[T]
    page: int
    page_size: int
    total: int
