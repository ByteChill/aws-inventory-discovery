import { createFileRoute, Link } from "@tanstack/react-router"
import { ChevronRight } from "lucide-react"
import { useMemo, useState } from "react"
import { useResourceTypes } from "@/api/hooks"
import {
  useAbnormalScopes,
  useAllAccounts,
  useScanSummary,
  useScans,
} from "@/api/scans"
import type { ScanRequestOut } from "@/client"
import {
  type Column,
  DataTable,
  type Option,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { InlineError } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { useScanColumns } from "@/features/columns"
import { ScanDialog } from "@/features/scan-dialog"
import {
  ScanResult,
  scanStatusText,
  scopeText,
  triggerText,
} from "@/features/scan-parts"
import { ScanSummaryCards } from "@/features/scan-summary"
import { useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { selectActive, selectClass } from "@/lib/filter-select"
import { formatDuration, formatNumber } from "@/lib/format"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/scans/")({
  validateSearch: tableSearchSchema,
  component: Scans,
})

const STATUSES = [
  "queued",
  "running",
  "succeeded",
  "partial",
  "failed",
  "cancelled",
]

function first(value: unknown): string | undefined {
  if (Array.isArray(value)) return value.length ? String(value[0]) : undefined
  return typeof value === "string" && value ? value : undefined
}

function Scans() {
  const { t, locale } = useI18n()
  const [formOpen, setFormOpen] = useState(false)
  const columns = useScanColumns()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const summary = useScanSummary()
  const scans = useScans(
    {
      trigger: first(state.filters.trigger),
      status: first(state.filters.status),
    },
    { page: state.page, pageSize: state.pageSize },
  )

  const facets = useMemo<Record<string, Option[]>>(
    () => ({
      trigger: [
        { value: "schedule", label: t("scans.trigger.scheduled") },
        { value: "api", label: t("scans.trigger.manual") },
      ],
      status: STATUSES.map((v) => ({ value: v, label: scanStatusText(t, v) })),
    }),
    [t],
  )

  function renderCell(column: Column, row: ScanRequestOut) {
    switch (column.key) {
      case "id":
        return (
          <Link
            to="/scans/$id"
            params={{ id: String(row.id) }}
            className="font-mono text-primary hover:underline"
          >
            #{row.id}
          </Link>
        )
      case "startedAt":
        return row.startedAt ? <Time value={row.startedAt} /> : "—"
      case "trigger":
        return <>{triggerText(t, row)}</>
      case "scope": {
        const text = scopeText(t, row)
        return (
          <span className="block truncate" title={text}>
            {text}
          </span>
        )
      }
      case "status":
        return <>{scanStatusText(t, row.status)}</>
      case "tasks": {
        const tasks = row.counts.tasks
        const total = Object.values(tasks).reduce(
          (sum, count) => sum + count,
          0,
        )
        const done = total - (tasks.queued ?? 0) - (tasks.running ?? 0)
        return (
          <span className="tabular-nums">
            {formatNumber(done, locale)} / {formatNumber(total, locale)}
          </span>
        )
      }
      case "duration":
        if (!row.startedAt) return "—"
        if (row.finishedAt)
          return formatDuration(
            new Date(row.finishedAt).getTime() -
              new Date(row.startedAt).getTime(),
            t,
          )
        return row.status === "running" || row.status === "queued"
          ? t("scans.column.inProgress")
          : "—"
      case "result":
        return (
          <div className="whitespace-normal">
            <ScanResult scan={row} />
          </div>
        )
    }
    return undefined
  }

  return (
    <Page>
      <div>
        <nav
          aria-label={t("scans.breadcrumb")}
          className="mb-3.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground"
        >
          <span>{t("nav.scanManagement")}</span>
          <ChevronRight aria-hidden="true" className="size-3" />
          <span aria-current="page">{t("scans.title")}</span>
        </nav>
        <PageHeader
          title={t("scans.title")}
          description={t("scans.subtitle")}
          actions={
            <Button aria-haspopup="dialog" onClick={() => setFormOpen(true)}>
              {t("scans.new")}
            </Button>
          }
        />
      </div>
      {summary.error && (
        <InlineError
          message={errorText(t, summary.error)}
          onRetry={() => summary.refetch()}
        />
      )}
      <ScanSummaryCards data={summary.data} isLoading={summary.isLoading} />
      {summary.data && summary.data.abnormalScopeCount > 0 && <AnomalyBlock />}
      <Section title={t("scans.history")}>
        <div className="min-w-0">
          <DataTable<ScanRequestOut>
            tableId="scans"
            entity="scans"
            columns={columns}
            rows={scans.data?.items ?? []}
            total={scans.data?.total ?? 0}
            state={state}
            onStateChange={setState}
            facets={facets}
            isLoading={scans.isLoading}
            error={scans.error}
            onRetry={() => scans.refetch()}
            getRowId={(row) => String(row.id)}
            columnWidths={{
              id: 80,
              status: 90,
              scope: 230,
              trigger: 85,
              tasks: 100,
              startedAt: 145,
              duration: 100,
              result: 250,
            }}
            columnMenu={false}
            filterPopovers={false}
            renderCell={renderCell}
            toolbar={
              <search
                className="flex flex-wrap items-center gap-2"
                aria-label={t("scans.historyFilters")}
              >
                <select
                  value={first(state.filters.status) ?? ""}
                  onChange={(e) => {
                    const filters = { ...state.filters }
                    if (e.target.value) filters.status = [e.target.value]
                    else delete filters.status
                    setState({ ...state, filters, page: 1 })
                  }}
                  aria-label={t("scans.column.status")}
                  className={cn(
                    selectClass,
                    first(state.filters.status) && selectActive,
                  )}
                >
                  <option value="">{t("scans.column.status")}</option>
                  {STATUSES.map((v) => (
                    <option key={v} value={v}>
                      {scanStatusText(t, v)}
                    </option>
                  ))}
                </select>
                <select
                  value={first(state.filters.trigger) ?? ""}
                  onChange={(e) => {
                    const filters = { ...state.filters }
                    if (e.target.value) filters.trigger = [e.target.value]
                    else delete filters.trigger
                    setState({ ...state, filters, page: 1 })
                  }}
                  aria-label={t("scans.column.trigger")}
                  className={cn(
                    selectClass,
                    first(state.filters.trigger) && selectActive,
                  )}
                >
                  <option value="">{t("scans.column.trigger")}</option>
                  <option value="schedule">
                    {t("scans.trigger.scheduled")}
                  </option>
                  <option value="api">{t("scans.trigger.manual")}</option>
                </select>
                {(state.filters.trigger || state.filters.status) && (
                  <Button
                    variant="link"
                    onClick={() => setState({ ...state, filters: {}, page: 1 })}
                  >
                    {t("action.clearFilters")}
                  </Button>
                )}
              </search>
            }
          />
        </div>
      </Section>
      <ScanDialog open={formOpen} onClose={() => setFormOpen(false)} />
    </Page>
  )
}

function AnomalyBlock() {
  const { t, locale } = useI18n()
  const query = useAbnormalScopes()
  const types = useResourceTypes()
  const accounts = useAllAccounts(true)
  const labels = useMemo(
    () => new Map(types.data?.map((x) => [x.typeName, x.label])),
    [types.data],
  )
  const names = useMemo(
    () => new Map(accounts.data?.map((a) => [a.accountId, a.accountName])),
    [accounts.data],
  )

  return (
    <Section
      title={t("scans.anomalies")}
      actions={
        <span className="text-muted-foreground">
          {t("scans.anomaliesHint")}
        </span>
      }
    >
      {query.error && (
        <InlineError
          message={errorText(t, query.error)}
          onRetry={() => query.refetch()}
        />
      )}
      {query.isLoading && (
        <p className="text-muted-foreground">{t("common.loading")}</p>
      )}
      <div className="relative overflow-auto rounded-lg border bg-card">
        <table className="w-full min-w-[820px] border-collapse text-left whitespace-nowrap">
          <thead>
            <tr className="border-b bg-subtle text-xs text-muted-foreground">
              <th scope="col" className="px-3.5 py-2 font-medium">
                {t("scanScope.account")}
              </th>
              <th scope="col" className="px-3.5 py-2 font-medium">
                {t("scans.anomalyColumn.region")}
              </th>
              <th scope="col" className="px-3.5 py-2 font-medium">
                {t("detail.type")}
              </th>
              <th scope="col" className="px-3.5 py-2 font-medium">
                {t("scanScope.detectedAt")}
              </th>
              <th scope="col" className="px-3.5 py-2 text-right font-medium">
                {t("scanScope.candidates")}
              </th>
              <th scope="col" className="px-3.5 py-2 text-right font-medium">
                {t("scanScope.active")}
              </th>
              <th scope="col" className="px-3.5 py-2">
                <span className="sr-only">{t("scans.reviewResources")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {query.data?.items.map((s) => {
              const name = names.get(s.accountId)
              return (
                <tr
                  key={s.scanShardId}
                  className="border-b last:border-b-0 hover:bg-subtle"
                >
                  <td className="px-3.5 py-2.5">
                    <Link
                      to="/accounts/$accountId"
                      params={{ accountId: s.accountId }}
                      className="font-medium text-primary hover:underline"
                    >
                      {name ?? s.accountId}
                    </Link>
                    {name && (
                      <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                        {s.accountId}
                      </span>
                    )}
                  </td>
                  <td className="px-3.5 py-2.5 font-mono text-muted-foreground">
                    {s.region}
                  </td>
                  <td className="px-3.5 py-2.5">
                    {labels.get(s.resourceType) ?? s.resourceType}
                  </td>
                  <td className="px-3.5 py-2.5 text-muted-foreground">
                    <Time value={s.guardDetectedAt} />
                  </td>
                  <td className="px-3.5 py-2.5 text-right tabular-nums">
                    <Link
                      to="/scan-scopes/$scanShardId"
                      params={{ scanShardId: String(s.scanShardId) }}
                      className="text-primary hover:underline"
                    >
                      {formatNumber(s.candidateCount, locale)}
                    </Link>
                  </td>
                  <td className="px-3.5 py-2.5 text-right tabular-nums">
                    <Link
                      to="/resources"
                      search={{
                        s: `acct:${s.accountId}`,
                        f: {
                          resourceType: [s.resourceType],
                          awsRegion: [s.region],
                        },
                      }}
                      className="text-primary hover:underline"
                    >
                      {formatNumber(s.activeCount, locale)}
                    </Link>
                  </td>
                  <td className="px-3.5 py-2.5 text-right">
                    <Button asChild variant="outline" size="sm">
                      <Link
                        to="/scan-scopes/$scanShardId"
                        params={{ scanShardId: String(s.scanShardId) }}
                      >
                        {t("scans.reviewResources")}
                      </Link>
                    </Button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Section>
  )
}
