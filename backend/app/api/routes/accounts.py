from fastapi import APIRouter

from app.api.deps import AccountFilterDep, CacheDep, ReadSessionDep
from app.core.config import get_settings
from app.query import accounts as accounts_query
from app.schemas.accounts import AccountDetail, AccountSummary, CoverageCell
from app.schemas.base import Page

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("")
def list_accounts(
    params: AccountFilterDep, session: ReadSessionDep, cache: CacheDep
) -> Page[AccountSummary]:
    return accounts_query.list_accounts(
        session, params, get_settings().INACTIVE_ACCOUNT_PURGE_DAYS, cache
    )


@router.get("/{account_id}")
def get_account(account_id: str, session: ReadSessionDep) -> AccountDetail:
    return accounts_query.get_account(
        session, account_id, get_settings().INACTIVE_ACCOUNT_PURGE_DAYS
    )


@router.get("/{account_id}/coverage")
def get_coverage(account_id: str, session: ReadSessionDep) -> list[CoverageCell]:
    return accounts_query.coverage(session, account_id)
