import { createFileRoute, Link } from "@tanstack/react-router"
import { useMemo } from "react"

import { useResourceTypes } from "@/api/hooks"
import {
  useCancelScan,
  useRetryFailed,
  useScan,
  useScanTasks,
} from "@/api/scans"
import type { ScanDetail, TaskOut } from "@/client"
import {
  type Column,
  DataTable,
  type Option,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { InlineError, QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { useTaskColumns } from "@/features/columns"
import {
  CurrentScopeResources,
  reasonText,
  ScanResult,
  scanStatusText,
  scopeText,
  triggerText,
} from "@/features/scan-parts"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { selectActive, selectClass } from "@/lib/filter-select"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/scans/$id")({
  validateSearch: tableSearchSchema,
  component: ScanPage,
})

const TASK_KINDS = [
  "sweep_account",
  "config_group",
  "shard",
  "resource",
  "confirm_missing",
]
const TASK_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"]
const ERROR_CODES = [
  "access_denied",
  "credential_error",
  "throttled",
  "endpoint_unreachable",
  "region_disabled",
  "unsupported",
  "registry_error",
  "worker_lost",
  "internal",
]

function first(value: unknown): string | undefined {
  if (Array.isArray(value)) return value.length ? String(value[0]) : undefined
  return typeof value === "string" && value ? value : undefined
}

function ScanPage() {
  const { t } = useI18n()
  const id = Number(Route.useParams().id)
  const query = useScan(id)
  return (
    <Page>
      <QueryRegion
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => query.refetch()}
        isEmpty={!query.data}
        emptyMessage={t("error.scan_not_found")}
      >
        {query.data && <Detail scan={query.data} />}
      </QueryRegion>
    </Page>
  )
}

function Detail({ scan }: { scan: ScanDetail }) {
  const { t } = useI18n()
  const retry = useRetryFailed(scan.id)
  const cancel = useCancelScan(scan.id)
  const active = scan.status === "queued" || scan.status === "running"
  const failedTasks = scan.counts.tasks.failed ?? 0
  const search = Route.useSearch()

  return (
    <>
      <div className="flex flex-col gap-2">
        <PageHeader
          title={
            <>
              {t("scans.detail.title")}{" "}
              <span className="font-mono text-base text-muted-foreground">
                {scan.id}
              </span>
            </>
          }
          actions={
            <>
              {failedTasks > 0 && (
                <Button
                  variant="outline"
                  disabled={retry.isPending}
                  onClick={() => retry.mutate()}
                >
                  {t("action.retryFailedTasks")}
                </Button>
              )}
              {active && (
                <Button
                  variant="outline"
                  disabled={cancel.isPending}
                  onClick={() => cancel.mutate()}
                >
                  {t("action.cancel")}
                </Button>
              )}
            </>
          }
        />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>{scanStatusText(t, scan.status)}</span>
          <span className="text-muted-foreground">
            {triggerText(t, scan)}
            {t("common.separator")}
            <Time value={scan.startedAt ?? scan.createdAt} />
          </span>
          {retry.data && (
            <span className="text-muted-foreground">
              {t("scans.detail.requeued", { count: retry.data.requeued })}
            </span>
          )}
        </div>
        {retry.error && <InlineError message={errorText(t, retry.error)} />}
        {cancel.error && <InlineError message={errorText(t, cancel.error)} />}
      </div>

      <Section title={t("scans.detail.scope")}>
        <p>{scopeText(t, scan)}</p>
      </Section>

      <Section
        title={t("scans.detail.counts")}
        actions={<CurrentScopeResources scan={scan} />}
      >
        <p>
          <ScanResult scan={scan} />
        </p>
      </Section>

      {scan.failuresByCode.length > 0 && (
        <Section title={t("scans.detail.failuresByReason")}>
          <ul className="flex flex-col gap-3">
            {scan.failuresByCode.map((f) => {
              const reason = reasonText(t, f.errorCode)
              return (
                <li key={f.errorCode}>
                  <Link
                    to="/scans/$id"
                    params={{ id: String(scan.id) }}
                    search={{
                      ...search,
                      f: { errorCode: [f.errorCode] },
                      page: undefined,
                    }}
                    className="block hover:text-primary"
                  >
                    <span className="font-medium">{reason.title}</span>
                    <span className="ml-3 text-muted-foreground">
                      {t("scans.failureTasks", { count: f.count })}
                    </span>
                    {reason.hint && (
                      <span className="block text-muted-foreground">
                        {reason.hint}
                      </span>
                    )}
                  </Link>
                </li>
              )
            })}
          </ul>
        </Section>
      )}

      <Section title={t("scans.detail.tasks")}>
        <TaskList scanId={scan.id} />
      </Section>
    </>
  )
}

function TaskList({ scanId }: { scanId: number }) {
  const { t } = useI18n()
  const columns = useTaskColumns()
  const types = useResourceTypes()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const params: Record<string, string> = {}
  for (const key of ["status", "kind", "errorCode"]) {
    const v = first(state.filters[key])
    if (v) params[key] = v
  }
  const query = useScanTasks(scanId, params, {
    page: state.page,
    pageSize: state.pageSize,
  })

  const facets = useMemo<Record<string, Option[]>>(
    () => ({
      kind: TASK_KINDS.map((v) => ({
        value: v,
        label: t(`tasks.kind.${v}` as MessageKey),
      })),
      status: TASK_STATUSES.map((v) => ({
        value: v,
        label: t(`tasks.status.${v}` as MessageKey),
      })),
      errorCode: ERROR_CODES.map((v) => ({
        value: v,
        label: reasonText(t, v).title,
      })),
    }),
    [t],
  )

  function renderCell(column: Column, row: TaskOut) {
    switch (column.key) {
      case "id":
        return (
          <Link
            to="/tasks/$id"
            params={{ id: String(row.id) }}
            className="text-primary hover:underline"
          >
            {row.id}
          </Link>
        )
      case "kind":
        return <>{t(`tasks.kind.${row.kind}` as MessageKey)}</>
      case "status":
        return <>{t(`tasks.status.${row.status}` as MessageKey)}</>
      case "attempt":
        return (
          <>{t("tasks.attempt", { n: row.attempt, max: row.maxAttempts })}</>
        )
      case "resourceType": {
        if (!row.resourceType) return null
        const label =
          types.data?.find((x) => x.typeName === row.resourceType)?.label ??
          row.resourceType
        return (
          <span className="block truncate" title={row.resourceType}>
            {label}
          </span>
        )
      }
      case "errorCode":
        return <>{row.errorCode ? reasonText(t, row.errorCode).title : ""}</>
      case "finishedAt":
        return <Time value={row.finishedAt} />
    }
    return undefined
  }

  function setFilter(key: string, value: string | undefined) {
    const filters = { ...state.filters }
    if (value) filters[key] = [value]
    else delete filters[key]
    setState({ ...state, filters, page: 1 })
  }

  const hasFilters = Object.keys(params).length > 0

  return (
    <div className="h-[34rem]">
      <DataTable<TaskOut>
        tableId="tasks"
        entity="tasks"
        columns={columns}
        rows={query.data?.items ?? []}
        total={query.data?.total ?? 0}
        state={state}
        onStateChange={setState}
        facets={facets}
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => query.refetch()}
        getRowId={(row) => String(row.id)}
        renderCell={renderCell}
        columnWidths={{
          id: 90,
          kind: 110,
          accountId: 160,
          region: 130,
          resourceType: 170,
          status: 90,
          attempt: 80,
          errorCode: 150,
          finishedAt: 130,
        }}
        filterPopovers={false}
        toolbar={
          <search
            className="flex flex-wrap items-center gap-2"
            aria-label={t("scans.detail.tasks")}
          >
            {(["kind", "status", "errorCode"] as const).map((key) => (
              <select
                key={key}
                value={params[key] ?? ""}
                onChange={(e) => setFilter(key, e.target.value || undefined)}
                aria-label={columns.find((c) => c.key === key)?.label}
                className={cn(selectClass, params[key] && selectActive)}
              >
                <option value="">
                  {columns.find((c) => c.key === key)?.label}
                </option>
                {facets[key].map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            ))}
            {hasFilters && (
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
  )
}
