from typing import Literal

from app.schemas.base import ApiModel


class Application(ApiModel):
    """One EIM: its accounts and resources. EIM, application name and BA are 1:1 (DESIGN 2)."""

    eim_id: str
    eim_app: str
    ba_id: str | None
    account_count: int
    environments: list[Literal["dev", "preprod", "prod"]]
    resource_count: int
