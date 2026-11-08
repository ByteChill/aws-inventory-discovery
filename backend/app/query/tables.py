"""The tables as Core objects, so SQL expressions on their columns type-check."""

from sqlalchemy import Table

from app.models import Account, AccountRegion, Resource, ResourceRelation, ScanShard


def _table(model: type) -> Table:
    table = model.__table__  # ty: ignore[unresolved-attribute]
    assert isinstance(table, Table)
    return table


RESOURCE = _table(Resource)
ACCOUNT = _table(Account)
ACCOUNT_REGION = _table(AccountRegion)
RELATION = _table(ResourceRelation)
SHARD = _table(ScanShard)

R = RESOURCE.c
A = ACCOUNT.c
S = SHARD.c
