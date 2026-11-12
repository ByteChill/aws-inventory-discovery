import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from "lucide-react"
import { type ReactNode, useState } from "react"

import { useApplications } from "@/api/hooks"
import type { Application } from "@/client"
import { tableSearchSchema, useTableUrlState } from "@/components/data-table"
import { EmptyState, InlineError } from "@/components/inline-state"
import { Page, PageHeader } from "@/components/layout/page"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatNumber } from "@/lib/format"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/apps/")({
  validateSearch: tableSearchSchema,
  component: Apps,
})

const ENVIRONMENTS = ["dev", "preprod", "prod"] as const
type SortKey = "eimApp" | "accountCount" | "resourceCount"

function Apps() {
  const { t } = useI18n()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const [q, setQ] = useState(state.q ?? "")
  const env = first(state.filters.env)
  const apps = useApplications(
    { q: state.q, accountType: env },
    {
      page: state.page,
      pageSize: state.pageSize,
      sort: state.sort ?? "eimApp",
    },
  )
  const total = apps.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / state.pageSize))
  const hasFilters = !!state.q || !!env
  const sort = state.sort ?? "eimApp"
  const sortKey = sort.replace(/^-/, "")
  const descending = sort.startsWith("-")

  function setEnv(value: string) {
    const filters = { ...state.filters }
    if (value) filters.env = [value]
    else delete filters.env
    setState({ ...state, filters, page: 1 })
  }

  function toggleSort(key: SortKey) {
    const next = sortKey === key ? (descending ? key : `-${key}`) : key
    setState({ ...state, sort: next, page: 1 })
  }

  function renderSort(key: SortKey) {
    return sortKey === key ? (
      descending ? (
        <ArrowDown className="size-3.5" />
      ) : (
        <ArrowUp className="size-3.5" />
      )
    ) : null
  }

  return (
    <Page className="gap-4">
      <PageHeader
        title={t("apps.title")}
        description={t("apps.subtitle", { count: total })}
      />

      <search className="flex flex-wrap items-center gap-2">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setState({ ...state, q: q.trim() || undefined, page: 1 })
          }}
          className="w-[min(380px,100%)]"
        >
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("apps.searchPlaceholder")}
            aria-label={t("apps.searchPlaceholder")}
            className="h-9 w-full rounded-md border border-input bg-surface px-2.5 placeholder:text-muted-foreground"
          />
        </form>
        <select
          value={env ?? ""}
          onChange={(e) => setEnv(e.target.value)}
          aria-label={t("apps.filterEnv")}
          className={cn(
            "h-9 rounded-md border border-input bg-surface px-2.5",
            env && "border-primary bg-primary-soft font-semibold text-primary",
          )}
        >
          <option value="">{t("apps.filterEnv")}</option>
          {ENVIRONMENTS.map((e) => (
            <option key={e} value={e}>
              {t(`env.${e}`)}
            </option>
          ))}
        </select>
        {(state.q || env) && (
          <Button
            variant="link"
            onClick={() => {
              setQ("")
              setState({ ...state, q: undefined, filters: {}, page: 1 })
            }}
          >
            {t("action.clearFilters")}
          </Button>
        )}
      </search>

      {apps.error ? (
        <InlineError
          message={errorText(t, apps.error)}
          onRetry={() => apps.refetch()}
        />
      ) : !apps.isLoading && total === 0 ? (
        <EmptyState
          message={
            hasFilters ? t("apps.empty") : t("table.emptyUnfiltered.apps")
          }
          action={
            hasFilters ? (
              <Button
                variant="outline"
                onClick={() => {
                  setQ("")
                  setState({ ...state, q: undefined, filters: {}, page: 1 })
                }}
              >
                {t("action.clearFilters")}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <div className="overflow-auto rounded-lg border bg-card">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b bg-subtle text-xs text-muted-foreground">
                  <Header>
                    <SortButton onClick={() => toggleSort("eimApp")}>
                      {t("apps.column.app")}
                      {renderSort("eimApp")}
                    </SortButton>
                  </Header>
                  <Header>{t("apps.column.eim")}</Header>
                  <Header>{t("apps.column.ba")}</Header>
                  <Header className="text-right">
                    <SortButton onClick={() => toggleSort("accountCount")}>
                      {t("apps.column.accounts")}
                      {renderSort("accountCount")}
                    </SortButton>
                  </Header>
                  <Header>{t("apps.column.environments")}</Header>
                  <Header className="text-right">
                    <SortButton onClick={() => toggleSort("resourceCount")}>
                      {t("apps.column.resources")}
                      {renderSort("resourceCount")}
                    </SortButton>
                  </Header>
                </tr>
              </thead>
              <tbody>
                {apps.data?.items.map((app) => (
                  <Row key={app.eimId} app={app} />
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
            <span>
              {t("table.total.apps", {
                count: total,
              })}
            </span>
            <div className="flex items-center gap-2">
              <span>{t("table.page", { page: state.page, pages })}</span>
              <Button
                variant="outline"
                size="icon"
                aria-label={t("table.prev")}
                disabled={state.page <= 1}
                onClick={() => setState({ ...state, page: state.page - 1 })}
              >
                <ChevronLeft />
              </Button>
              <Button
                variant="outline"
                size="icon"
                aria-label={t("table.next")}
                disabled={state.page >= pages}
                onClick={() => setState({ ...state, page: state.page + 1 })}
              >
                <ChevronRight />
              </Button>
            </div>
          </div>
        </>
      )}
    </Page>
  )
}

function Row({ app }: { app: Application }) {
  const { t, locale } = useI18n()
  return (
    <tr className="border-b last:border-b-0 hover:bg-subtle">
      <td className="px-3.5 py-2.5">
        <Link
          to="/"
          search={{ s: `app:${app.eimId}` }}
          className="font-semibold text-primary hover:underline"
        >
          {app.eimApp}
        </Link>
      </td>
      <td className="px-3.5 py-2.5 font-mono text-xs text-muted-foreground">
        {app.eimId}
      </td>
      <td className="px-3.5 py-2.5 font-mono text-xs text-muted-foreground">
        {app.baId ?? ""}
      </td>
      <td className="px-3.5 py-2.5 text-right">{app.accountCount}</td>
      <td className="px-3.5 py-2.5 text-muted-foreground">
        {app.environments.map((e) => t(`env.${e}`)).join(t("common.separator"))}
      </td>
      <td className="px-3.5 py-2.5 text-right">
        {formatNumber(app.resourceCount, locale)}
      </td>
    </tr>
  )
}

function Header({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <th scope="col" className={cn("px-3.5 py-2 font-medium", className)}>
      {children}
    </th>
  )
}

function SortButton({
  children,
  onClick,
}: {
  children: ReactNode
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 hover:text-foreground"
    >
      {children}
    </button>
  )
}

function first(value: unknown): string | undefined {
  if (Array.isArray(value)) return value.length ? String(value[0]) : undefined
  return typeof value === "string" && value ? value : undefined
}
