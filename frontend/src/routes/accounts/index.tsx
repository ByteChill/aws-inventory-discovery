import { createFileRoute, Link } from "@tanstack/react-router"
import { useMemo } from "react"

import { useAccounts } from "@/api/hooks"
import type { AccountSummary } from "@/client"
import {
  type Column,
  DataTable,
  type Option,
  type TableState,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { Time } from "@/components/time"
import { useAccountColumns } from "@/features/columns"
import { type MessageKey, useI18n } from "@/i18n"
import { formatNumber } from "@/lib/format"

export const Route = createFileRoute("/accounts/")({
  validateSearch: tableSearchSchema,
  component: Accounts,
})

const DAY = 86_400_000
const ENVIRONMENTS = ["dev", "preprod", "prod"]
const ACCESS_STATUSES = ["ok", "access_denied", "credential_error", "partial"]

function list(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String)
  return typeof value === "string" && value ? [value] : []
}

/** Table state to the accounts API; `removed` is the inverse of its `active` flag. */
function accountParams(state: TableState) {
  const params: Record<string, string | string[] | boolean> = {}
  for (const key of ["accountType", "eimId", "baId", "accessStatus"]) {
    const values = list(state.filters[key])
    if (values.length) params[key] = values
  }
  if (state.q) params.q = state.q
  if (list(state.filters.removed).length) params.active = false
  return params
}

function Accounts() {
  const { t, locale } = useI18n()
  const columns = useAccountColumns()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const query = useAccounts(accountParams(state), {
    page: state.page,
    pageSize: state.pageSize,
    sort: state.sort,
  })

  const facets = useMemo<Record<string, Option[]>>(
    () => ({
      accountType: ENVIRONMENTS.map((v) => ({
        value: v,
        label: t(`env.${v}` as MessageKey),
      })),
      removed: [{ value: "true", label: t("common.yes") }],
      accessStatus: ACCESS_STATUSES.map((v) => ({
        value: v,
        label: t(`access.${v}` as MessageKey),
      })),
    }),
    [t],
  )

  function renderCell(column: Column, row: AccountSummary) {
    switch (column.key) {
      case "accountName":
        return (
          <Link
            to="/accounts/$accountId"
            params={{ accountId: row.accountId }}
            className="text-primary hover:underline"
          >
            {row.accountName}
          </Link>
        )
      case "resourceCount":
        return (
          <Link
            to="/resources"
            search={{ f: { accountId: [row.accountId] } }}
            className="hover:text-primary hover:underline"
          >
            {formatNumber(row.resourceCount, locale)}
          </Link>
        )
      case "accessStatus": {
        // Only abnormal states get text; a removed account says when it is cleaned up.
        const parts: string[] = []
        if (row.accessStatus !== "ok")
          parts.push(t(`access.${row.accessStatus}` as MessageKey))
        if (row.purgeAt) {
          const days = Math.max(
            0,
            Math.ceil((new Date(row.purgeAt).getTime() - Date.now()) / DAY),
          )
          parts.push(t("account.purgeIn", { days }))
        }
        return <>{parts.join(t("common.separator"))}</>
      }
      case "lastScanOkAt":
        return <Time value={row.lastScanOkAt} />
      case "accountType":
        return <>{t(`env.${row.accountType}`)}</>
    }
    return undefined
  }

  return (
    <div className="flex h-full flex-col gap-4 px-8 py-6 max-[1180px]:px-5 max-[800px]:px-4">
      <h1>{t("accounts.title")}</h1>
      <div className="min-h-0 flex-1">
        <DataTable<AccountSummary>
          tableId="accounts"
          entity="accounts"
          columns={columns}
          rows={query.data?.items ?? []}
          total={query.data?.total ?? 0}
          state={state}
          onStateChange={setState}
          facets={facets}
          filterLabels={{ removed: t("accounts.removed") }}
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => query.refetch()}
          getRowId={(row) => row.accountId}
          renderCell={renderCell}
          toolbarStart={
            <>
              <input
                type="search"
                defaultValue={state.q ?? ""}
                key={state.q ?? ""}
                placeholder={t("accounts.searchPlaceholder")}
                aria-label={t("accounts.searchPlaceholder")}
                onKeyDown={(e) => {
                  if (e.key === "Enter")
                    setState({
                      ...state,
                      q: e.currentTarget.value.trim() || undefined,
                      page: 1,
                    })
                }}
                className="h-8 w-64 rounded-md border border-input bg-card px-2.5 placeholder:text-muted-foreground"
              />
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={list(state.filters.removed).length > 0}
                  onChange={(e) => {
                    const filters = { ...state.filters }
                    if (e.target.checked) filters.removed = ["true"]
                    else delete filters.removed
                    setState({ ...state, filters, page: 1 })
                  }}
                  className="accent-[var(--accent)]"
                />
                {t("accounts.removed")}
              </label>
            </>
          }
        />
      </div>
    </div>
  )
}
