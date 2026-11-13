import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"

import { useAccounts, useFacets } from "@/api/hooks"
import type { AccountSummary } from "@/client"
import { CopyButton } from "@/components/copy-button"
import {
  type Column,
  DataTable,
  type Option,
  type TableState,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { Page, PageHeader } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { useAccountColumns } from "@/features/columns"
import { type MessageKey, useI18n } from "@/i18n"
import { selectActive, selectClass } from "@/lib/filter-select"
import { formatNumber } from "@/lib/format"
import { cn } from "@/lib/utils"

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

function single(value: unknown): string | undefined {
  return list(value)[0]
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
  const navigate = useNavigate()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const query = useAccounts(accountParams(state), {
    page: state.page,
    pageSize: state.pageSize,
    sort: state.sort,
  })
  // Application names for the EIM filter come from the resource facets.
  const eimFacets = useFacets(["eimId"], {})
  const total = query.data?.total ?? 0

  const [draft, setDraft] = useState(state.q ?? "")
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    const id = setTimeout(() => {
      if ((state.q ?? "") === draft.trim()) return
      setState({ ...state, q: draft.trim() || undefined, page: 1 })
    }, 350)
    return () => clearTimeout(id)
  }, [draft, state, setState])
  useEffect(() => {
    setDraft(state.q ?? "")
  }, [state.q])

  const facets = useMemo<Record<string, Option[]>>(
    () => ({
      accountType: ENVIRONMENTS.map((v) => ({
        value: v,
        label: t(`env.${v}` as MessageKey),
      })),
      eimId: eimFacets.data?.eimId ?? [],
      removed: [{ value: "true", label: t("common.yes") }],
      accessStatus: ACCESS_STATUSES.map((v) => ({
        value: v,
        label: t(`access.${v}` as MessageKey),
      })),
    }),
    [t, eimFacets.data],
  )

  function setFilter(key: string, value: string | undefined) {
    const filters = { ...state.filters }
    if (value) filters[key] = [value]
    else delete filters[key]
    setState({ ...state, filters, page: 1 })
  }

  function renderCell(column: Column, row: AccountSummary) {
    switch (column.key) {
      case "accountName":
        return (
          <Link
            to="/accounts/$accountId"
            params={{ accountId: row.accountId }}
            title={row.accountName}
            className="block truncate font-semibold text-primary hover:underline"
          >
            {row.accountName}
          </Link>
        )
      case "accountId":
        return <span className="font-mono text-xs">{row.accountId}</span>
      case "eimId":
        if (!row.eimId) return null
        return (
          <div className="min-w-0">
            {row.eimApp && (
              <Link
                to="/"
                search={{ s: `app:${row.eimId}` }}
                title={row.eimApp}
                className="block truncate text-primary hover:underline"
              >
                {row.eimApp}
              </Link>
            )}
            <span className="flex min-w-0 items-center gap-0.5 font-mono text-xs text-muted-foreground">
              <span className="truncate">{row.eimId}</span>
              <CopyButton value={row.eimId} className="size-5 shrink-0" />
            </span>
          </div>
        )
      case "resourceCount":
        return (
          <Link
            to="/resources"
            search={{ s: `acct:${row.accountId}` }}
            className="hover:text-primary hover:underline"
          >
            {formatNumber(row.resourceCount, locale)}
          </Link>
        )
      case "accessStatus": {
        // Only abnormal states get text; a removed account says when it is cleaned up.
        const purgeAt = row.purgeAt
          ? Math.max(
              0,
              Math.ceil((new Date(row.purgeAt).getTime() - Date.now()) / DAY),
            )
          : null
        if (row.accessStatus === "ok" && purgeAt === null) return null
        return (
          <div className="flex flex-col">
            {row.accessStatus !== "ok" && (
              <span className="text-warn">
                {t(`access.${row.accessStatus}` as MessageKey)}
              </span>
            )}
            {purgeAt !== null && (
              <span className="text-xs text-muted-foreground">
                {t("account.purgeIn", { days: purgeAt })}
              </span>
            )}
          </div>
        )
      }
      case "lastScanOkAt":
        return <Time value={row.lastScanOkAt} />
      case "accountType":
        return <>{t(`env.${row.accountType}`)}</>
    }
    return undefined
  }

  const hasFilters =
    !!state.q ||
    ["accountType", "eimId", "baId", "accessStatus", "removed"].some(
      (key) => list(state.filters[key]).length > 0,
    )

  return (
    <Page className="min-h-0 gap-4">
      <PageHeader
        title={t("accounts.title")}
        description={t("accounts.subtitle", { count: total })}
      />
      <div className="min-h-0 flex-1">
        <DataTable<AccountSummary>
          tableId="accounts"
          entity="accounts"
          columns={columns}
          rows={query.data?.items ?? []}
          total={total}
          state={state}
          onStateChange={setState}
          facets={facets}
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => query.refetch()}
          getRowId={(row) => row.accountId}
          columnWidths={{
            accountName: 220,
            accountId: 150,
            accountType: 90,
            eimId: 170,
            baId: 130,
            resourceCount: 90,
            accessStatus: 150,
            lastScanOkAt: 150,
          }}
          filterPopovers={false}
          onRowActivate={(row) =>
            navigate({
              to: "/accounts/$accountId",
              params: { accountId: row.accountId },
            })
          }
          rowLabel={(row) => t("accounts.openRow", { name: row.accountName })}
          renderCell={renderCell}
          toolbar={
            <search
              className="flex flex-wrap items-center gap-2"
              aria-label={t("accounts.searchPlaceholder")}
            >
              <label className="flex h-9 min-w-[200px] flex-1 basis-[220px] items-center gap-2 rounded-md border border-input bg-card px-2.5 text-muted-foreground focus-within:outline-2 focus-within:outline-primary focus-within:outline-offset-1 sm:max-w-[380px]">
                <Search aria-hidden="true" className="size-4 shrink-0" />
                <input
                  type="search"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter") return
                    e.preventDefault()
                    setState({
                      ...state,
                      q: draft.trim() || undefined,
                      page: 1,
                    })
                  }}
                  placeholder={t("accounts.searchPlaceholder")}
                  aria-label={t("accounts.searchPlaceholder")}
                  className="h-full min-w-0 flex-1 bg-transparent text-foreground placeholder:text-muted-foreground focus-visible:outline-0"
                />
              </label>
              <select
                value={single(state.filters.eimId) ?? ""}
                onChange={(e) =>
                  setFilter("eimId", e.target.value || undefined)
                }
                aria-label="EIM"
                className={cn(
                  selectClass,
                  single(state.filters.eimId) && selectActive,
                )}
              >
                <option value="">EIM</option>
                {(eimFacets.data?.eimId ?? []).map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <select
                value={single(state.filters.accountType) ?? ""}
                onChange={(e) =>
                  setFilter("accountType", e.target.value || undefined)
                }
                aria-label={t("env.label")}
                className={cn(
                  selectClass,
                  single(state.filters.accountType) && selectActive,
                )}
              >
                <option value="">{t("env.label")}</option>
                {ENVIRONMENTS.map((v) => (
                  <option key={v} value={v}>
                    {t(`env.${v}` as MessageKey)}
                  </option>
                ))}
              </select>
              <select
                value={single(state.filters.accessStatus) ?? ""}
                onChange={(e) =>
                  setFilter("accessStatus", e.target.value || undefined)
                }
                aria-label={t("accounts.column.status")}
                className={cn(
                  selectClass,
                  single(state.filters.accessStatus) && selectActive,
                )}
              >
                <option value="">{t("accounts.column.status")}</option>
                {ACCESS_STATUSES.map((v) => (
                  <option key={v} value={v}>
                    {t(`access.${v}` as MessageKey)}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-2 whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={list(state.filters.removed).length > 0}
                  onChange={(e) =>
                    setFilter("removed", e.target.checked ? "true" : undefined)
                  }
                  className="accent-[var(--accent)]"
                />
                {t("accounts.removed")}
              </label>
              {hasFilters && (
                <Button
                  variant="link"
                  onClick={() => {
                    setDraft("")
                    setState({ ...state, q: undefined, filters: {}, page: 1 })
                  }}
                >
                  {t("action.clearFilters")}
                </Button>
              )}
            </search>
          }
        />
      </div>
    </Page>
  )
}
