import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"

import { useApplications } from "@/api/hooks"
import type { Application } from "@/client"
import {
  type Column,
  DataTable,
  type Option,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { Page, PageHeader } from "@/components/layout/page"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/apps/")({
  validateSearch: tableSearchSchema,
  component: Apps,
})

const ENVIRONMENTS = ["dev", "preprod", "prod"] as const
const DEFAULT_SORT = "-resourceCount"

const selectClass =
  "h-9 max-w-[180px] flex-1 rounded-md border border-input bg-card px-2 text-foreground min-[801px]:flex-none"
const selectActive = "border-primary bg-primary-soft font-semibold text-primary"

function Apps() {
  const { t } = useI18n()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const navigate = useNavigate()
  const env = single(state.filters.environments)
  const apps = useApplications(
    { q: state.q, accountType: env },
    {
      page: state.page,
      pageSize: state.pageSize,
      sort: state.sort ?? DEFAULT_SORT,
    },
  )
  const total = apps.data?.total ?? 0

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

  const envOptions = useMemo<Option[]>(
    () => ENVIRONMENTS.map((value) => ({ value, label: t(`env.${value}`) })),
    [t],
  )

  const columns = useMemo<Column[]>(
    () => [
      {
        key: "eimApp",
        label: t("apps.column.app"),
        type: "text",
        mono: false,
        sortable: true,
        filterable: false,
        scope: "common",
      },
      {
        key: "eimId",
        label: t("apps.column.eim"),
        type: "text",
        mono: true,
        sortable: false,
        filterable: false,
        scope: "common",
      },
      {
        key: "baId",
        label: t("apps.column.ba"),
        type: "text",
        mono: true,
        sortable: false,
        filterable: false,
        scope: "common",
      },
      {
        key: "accountCount",
        label: t("apps.column.accounts"),
        type: "number",
        mono: false,
        sortable: true,
        filterable: false,
        scope: "common",
      },
      {
        key: "environments",
        label: t("apps.column.environments"),
        type: "text",
        mono: false,
        sortable: false,
        filterable: false,
        scope: "common",
      },
      {
        key: "resourceCount",
        label: t("apps.column.resources"),
        type: "number",
        mono: false,
        sortable: true,
        filterable: false,
        scope: "common",
      },
    ],
    [t],
  )

  function open(app: Application) {
    navigate({ to: "/", search: { s: `app:${app.eimId}` } })
  }

  const hasFilters = !!state.q || !!env

  return (
    <Page className="min-h-0 gap-4">
      <PageHeader
        title={t("apps.title")}
        description={t("apps.subtitle", { count: total })}
      />

      <div className="min-h-0 flex-1">
        <DataTable<Application>
          tableId="apps"
          entity="apps"
          columns={columns}
          rows={apps.data?.items ?? []}
          total={total}
          state={{ ...state, sort: state.sort ?? DEFAULT_SORT }}
          onStateChange={setState}
          facets={{ environments: envOptions }}
          isLoading={apps.isLoading}
          error={apps.error}
          onRetry={() => apps.refetch()}
          getRowId={(app) => app.eimId}
          columnWidths={{
            eimApp: 260,
            eimId: 140,
            baId: 140,
            accountCount: 110,
            environments: 170,
            resourceCount: 110,
          }}
          filterPopovers={false}
          onRowActivate={open}
          rowLabel={(app) => t("apps.openRow", { name: app.eimApp })}
          renderCell={(column, app) => {
            if (column.key === "eimApp")
              return (
                <Link
                  to="/"
                  search={{ s: `app:${app.eimId}` }}
                  title={app.eimApp}
                  className="block truncate font-semibold text-primary hover:underline"
                >
                  {app.eimApp}
                </Link>
              )
            if (column.key === "environments")
              return (
                <span className="block truncate">
                  {app.environments
                    .map((e) => t(`env.${e}`))
                    .join(t("common.separator"))}
                </span>
              )
            return undefined
          }}
          toolbar={
            <search
              className="flex flex-wrap items-center gap-2"
              aria-label={t("apps.searchPlaceholder")}
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
                  placeholder={t("apps.searchPlaceholder")}
                  aria-label={t("apps.searchPlaceholder")}
                  className="h-full min-w-0 flex-1 bg-transparent text-foreground placeholder:text-muted-foreground focus-visible:outline-0"
                />
              </label>
              <select
                value={env ?? ""}
                onChange={(e) => {
                  const filters = { ...state.filters }
                  if (e.target.value) filters.environments = [e.target.value]
                  else delete filters.environments
                  setState({ ...state, filters, page: 1 })
                }}
                aria-label={t("env.label")}
                className={cn(selectClass, env && selectActive)}
              >
                <option value="">{t("env.label")}</option>
                {ENVIRONMENTS.map((e) => (
                  <option key={e} value={e}>
                    {t(`env.${e}`)}
                  </option>
                ))}
              </select>
              {hasFilters && (
                <Button
                  variant="link"
                  onClick={() => {
                    setDraft("")
                    setState({
                      ...state,
                      q: undefined,
                      filters: {},
                      page: 1,
                    })
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

function single(value: unknown): string | undefined {
  if (Array.isArray(value)) return value.length ? String(value[0]) : undefined
  return typeof value === "string" && value ? value : undefined
}
