import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"

import {
  useDeleteAccount,
  useImportAccounts,
  useRestoreAccount,
} from "@/api/accounts"
import { useAccounts, useFacets } from "@/api/hooks"
import type { AccountSelector, AccountSummary } from "@/client"
import { CopyButton } from "@/components/copy-button"
import {
  type Column,
  DataTable,
  type Option,
  type TableState,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { Breadcrumb } from "@/components/layout/crumbs"
import { Page, PageHeader } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { AccountEditor } from "@/features/account-editor"
import { BulkPolicyDialog } from "@/features/bulk-policy"
import { useAccountColumns } from "@/features/columns"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
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
  const scan = single(state.filters.scanEnabled)
  if (scan) params.scanEnabled = scan === "true"
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
  const remove = useDeleteAccount()
  const restore = useRestoreAccount()
  const importAccounts = useImportAccounts()
  const pageError = importAccounts.error ?? restore.error
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<AccountSummary | undefined>()
  const [deleting, setDeleting] = useState<AccountSummary | null>(null)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const showingRemoved = list(state.filters.removed).length > 0

  const filterSelector = useMemo<AccountSelector>(() => {
    const selector: AccountSelector = {}
    const eimIds = list(state.filters.eimId)
    const baIds = list(state.filters.baId)
    const accountTypes = list(state.filters.accountType)
    const accessStatuses = list(state.filters.accessStatus)
    if (eimIds.length) selector.eimIds = eimIds
    if (baIds.length) selector.baIds = baIds
    if (accountTypes.length) {
      selector.accountTypes = accountTypes as AccountSelector["accountTypes"]
    }
    if (accessStatuses.length) selector.accessStatuses = accessStatuses
    if (state.q) selector.q = state.q
    const scan = single(state.filters.scanEnabled)
    if (scan) selector.scanEnabled = scan === "true"
    return Object.keys(selector).length ? selector : { all: true }
  }, [state.filters, state.q])

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
            to="/"
            search={{ s: `acct:${row.accountId}` }}
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
      case "scanEnabled":
        return (
          <span
            className={
              row.scanEnabled ? "text-primary" : "text-muted-foreground"
            }
          >
            {t(
              row.scanEnabled
                ? "accounts.scanIncluded"
                : "accounts.scanStopped",
            )}
          </span>
        )
      case "actions":
        return (
          <div className="flex items-center gap-2">
            <Button
              variant="link"
              size="sm"
              className="px-0"
              onClick={() => {
                setEditing(row)
                setEditorOpen(true)
              }}
            >
              {t("accounts.edit")}
            </Button>
            {row.active ? (
              <Button
                variant="link"
                size="sm"
                className="px-0 text-danger"
                onClick={() => setDeleting(row)}
              >
                {t("accounts.delete")}
              </Button>
            ) : (
              <Button
                variant="link"
                size="sm"
                className="px-0"
                disabled={restore.isPending}
                onClick={() =>
                  restore.mutate(row.accountId, {
                    onSuccess: () =>
                      setNotice(
                        t("accounts.restored", { name: row.accountName }),
                      ),
                  })
                }
              >
                {t("accounts.restore")}
              </Button>
            )}
          </div>
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
      case "coverage":
        return (
          <Link
            to="/accounts/$accountId"
            params={{ accountId: row.accountId }}
            className="text-primary hover:underline"
          >
            {t("accounts.coverage")}
          </Link>
        )
      case "accountType":
        return <>{t(`env.${row.accountType}`)}</>
    }
    return undefined
  }

  const hasFilters =
    !!state.q ||
    [
      "accountType",
      "eimId",
      "baId",
      "accessStatus",
      "removed",
      "scanEnabled",
    ].some((key) => list(state.filters[key]).length > 0)

  return (
    <Page className="min-h-0 gap-4">
      <div className="flex flex-col gap-3">
        <Breadcrumb
          items={[
            {
              key: "inventory",
              content: (
                <Link to="/" search={{ s: undefined }}>
                  {t("nav.inventory")}
                </Link>
              ),
            },
            { key: "accounts", content: t("accounts.title") },
          ]}
        />
        <PageHeader
          title={t("accounts.title")}
          description={t("accounts.subtitle", { count: total })}
          actions={
            <>
              <Button
                variant="outline"
                disabled={importAccounts.isPending}
                onClick={() =>
                  importAccounts.mutate(undefined, {
                    onSuccess: (result) =>
                      setNotice(
                        t("accounts.imported", {
                          imported: result.imported,
                          skipped: result.skipped,
                        }),
                      ),
                  })
                }
              >
                {t("accounts.import")}
              </Button>
              <Button
                onClick={() => {
                  setEditing(undefined)
                  setEditorOpen(true)
                }}
              >
                {t("accounts.add")}
              </Button>
            </>
          }
        />
        {(notice || pageError) && (
          <p
            role="status"
            className={pageError ? "text-danger" : "text-muted-foreground"}
          >
            {pageError ? errorText(t, pageError) : notice}
          </p>
        )}
      </div>
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
            accountName: 190,
            accountId: 130,
            accountType: 84,
            eimId: 150,
            baId: 110,
            resourceCount: 78,
            scanEnabled: 105,
            accessStatus: 100,
            lastScanOkAt: 120,
            coverage: 90,
            actions: 140,
          }}
          columnMenu={false}
          filterPopovers={false}
          onRowActivate={(row) =>
            navigate({
              to: "/",
              search: { s: `acct:${row.accountId}` },
            })
          }
          rowLabel={(row) => t("accounts.openRow", { name: row.accountName })}
          renderCell={renderCell}
          selection={
            showingRemoved
              ? undefined
              : {
                  selected,
                  onChange: setSelected,
                  label: (row) =>
                    t("accounts.selectRow", { name: row.accountName }),
                }
          }
          toolbarEnd={
            showingRemoved ? undefined : (
              <Button
                variant="outline"
                disabled={total === 0}
                onClick={() => setBulkOpen(true)}
              >
                {t("accounts.bulkPolicy")}
                {selected.size > 0
                  ? ` (${formatNumber(selected.size, locale)})`
                  : ""}
              </Button>
            )
          }
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
                aria-label={t("accounts.accessStatus")}
                className={cn(
                  selectClass,
                  single(state.filters.accessStatus) && selectActive,
                )}
              >
                <option value="">{t("accounts.accessStatus")}</option>
                {ACCESS_STATUSES.map((v) => (
                  <option key={v} value={v}>
                    {t(`access.${v}` as MessageKey)}
                  </option>
                ))}
              </select>
              <select
                value={single(state.filters.scanEnabled) ?? ""}
                onChange={(e) =>
                  setFilter("scanEnabled", e.target.value || undefined)
                }
                aria-label={t("accounts.column.scan")}
                className={cn(
                  selectClass,
                  single(state.filters.scanEnabled) && selectActive,
                )}
              >
                <option value="">{t("accounts.column.scan")}</option>
                <option value="true">{t("accounts.scanIncluded")}</option>
                <option value="false">{t("accounts.scanStopped")}</option>
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
        <AccountEditor
          open={editorOpen}
          account={editing}
          onClose={() => setEditorOpen(false)}
          onSaved={(saved) => {
            setNotice(t("accounts.saved", { name: saved.accountName }))
            if (!editing) {
              navigate({
                to: "/accounts/$accountId",
                params: { accountId: saved.accountId },
              })
            }
          }}
        />
        <BulkPolicyDialog
          open={bulkOpen}
          selectedIds={[...selected]}
          filterSelector={filterSelector}
          filterCount={total}
          onClose={() => setBulkOpen(false)}
          onUpdated={(updated) => {
            setSelected(new Set())
            setNotice(t("accounts.bulkUpdated", { count: updated }))
          }}
        />
        <Dialog
          open={deleting !== null}
          onClose={() => setDeleting(null)}
          title={t("accounts.confirmDeleteTitle")}
        >
          <p>
            {t("accounts.confirmDeleteBody", {
              name: deleting?.accountName ?? "",
            })}
          </p>
          <p className="text-muted-foreground">{t("accounts.deleteNote")}</p>
          {remove.error && (
            <p className="text-danger">{errorText(t, remove.error)}</p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setDeleting(null)}>
              {t("action.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => {
                if (!deleting) return
                remove.mutate(deleting.accountId, {
                  onSuccess: () => {
                    setNotice(
                      t("accounts.deleted", { name: deleting.accountName }),
                    )
                    setDeleting(null)
                  },
                })
              }}
            >
              {t("accounts.delete")}
            </Button>
          </div>
        </Dialog>
      </div>
    </Page>
  )
}
