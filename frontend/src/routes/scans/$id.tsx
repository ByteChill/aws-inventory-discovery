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
import { Input } from "@/components/ui/input"
import {
  ResourceMetrics,
  reasonText,
  ScanBreadcrumbs,
  ScanDuration,
  SummaryField,
  scanStatusText,
  scopeText,
  TaskResult,
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
const TASK_STATUSES = [
  "succeeded",
  "failed",
  "running",
  "queued",
  "cancelled",
  "skipped",
]
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
  const query = useScan(Number(Route.useParams().id))
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
  const types = useResourceTypes()
  const active = scan.status === "queued" || scan.status === "running"
  const failedTasks = scan.counts.tasks.failed ?? 0
  const total = Object.values(scan.counts.tasks).reduce(
    (sum, count) => sum + count,
    0,
  )
  const done = ["succeeded", "failed", "cancelled", "skipped"].reduce(
    (sum, status) => sum + (scan.counts.tasks[status] ?? 0),
    0,
  )
  const search = Route.useSearch()
  const typeLabel = (name: string) =>
    types.data?.find((type) => type.typeName === name)?.label ?? name
  const scope = scan.scope
  const accounts = scope?.accountIds?.length
    ? scope.accountIds.join(t("common.separator"))
    : (scan.target?.accountId ?? t("scans.scope.allAccounts"))
  const regions = scope?.awsRegions?.length
    ? scope.awsRegions.join(t("common.separator"))
    : (scan.target?.awsRegion ?? t("scans.scope.allRegions"))
  const resourceTypes = scope?.resourceTypes?.length
    ? scope.resourceTypes.map(typeLabel).join(t("common.separator"))
    : scan.target?.resourceType
      ? typeLabel(scan.target.resourceType)
      : t("scans.scope.allTypes")

  return (
    <>
      <div>
        <ScanBreadcrumbs scanId={scan.id} />
        <PageHeader
          title={
            <>
              {t("scans.detail.title")}{" "}
              <span className="font-mono">#{scan.id}</span>
            </>
          }
          description={
            <>
              {triggerText(t, scan)}
              {t("common.separator")}
              {scopeText(t, scan)}
            </>
          }
          actions={
            <>
              {failedTasks > 0 && (
                <Button
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
                  {t("scans.detail.cancel" as MessageKey)}
                </Button>
              )}
              <Button
                asChild
                variant={scan.status === "succeeded" ? "default" : "outline"}
              >
                <Link to="/resources" search={{ scanRequestId: scan.id }}>
                  {t("scans.viewScopeResources")}
                </Link>
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  void navigator.clipboard.writeText(location.href)
                }}
              >
                {t("action.copyLink")}
              </Button>
            </>
          }
        />
        {retry.data && (
          <p className="mt-3 text-muted-foreground">
            {t("scans.detail.requeued", { count: retry.data.requeued })}
          </p>
        )}
        {retry.error && <InlineError message={errorText(t, retry.error)} />}
        {cancel.error && <InlineError message={errorText(t, cancel.error)} />}
      </div>

      {scan.failuresByCode.length > 0 && (
        <section className="rounded-xl border bg-subtle px-5 py-4">
          <h2>{t("scans.detail.failuresByReason")}</h2>
          <ul className="mt-3 flex flex-wrap gap-2">
            {scan.failuresByCode.map((failure) => {
              const reason = reasonText(t, failure.errorCode)
              return (
                <li key={failure.errorCode}>
                  <Link
                    to="/scans/$id"
                    params={{ id: String(scan.id) }}
                    search={{
                      ...search,
                      f: { ...search.f, errorCode: [failure.errorCode] },
                      page: undefined,
                    }}
                    title={reason.hint ?? undefined}
                    className={cn(
                      "inline-flex items-center gap-2 rounded-md border px-3 py-1.5 hover:border-primary hover:text-primary",
                      first(search.f?.errorCode) === failure.errorCode &&
                        "border-primary text-primary",
                    )}
                  >
                    {reason.title}
                    <span className="tabular-nums">{failure.count}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      <section className="rounded-xl border bg-background px-5 py-4">
        <h2 className="mb-3">{t("scans.detail.summary" as MessageKey)}</h2>
        {active && (
          <progress
            aria-label={t("scans.detail.progress" as MessageKey)}
            value={done}
            max={Math.max(1, total)}
            className="mb-4 block h-1.5 w-full overflow-hidden rounded bg-subtle accent-primary [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:bg-subtle [&::-webkit-progress-value]:bg-primary"
          />
        )}
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 min-[1180px]:grid-cols-4">
          <SummaryField label={t("scans.column.status")}>
            <span>{scanStatusText(t, scan.status)}</span>
            {active && (
              <span className="ml-2 text-xs text-muted-foreground">
                {t("scans.detail.autoRefresh" as MessageKey)}
              </span>
            )}
          </SummaryField>
          <SummaryField label={t("scans.detail.progress" as MessageKey)}>
            {t("scans.detail.taskProgress" as MessageKey, { done, total })}
          </SummaryField>
          <SummaryField label={t("scans.column.startedAt")}>
            {scan.startedAt ? (
              <Time value={scan.startedAt} />
            ) : (
              t("scans.detail.notStarted" as MessageKey)
            )}
          </SummaryField>
          <SummaryField
            label={
              active
                ? t("scans.detail.elapsed" as MessageKey)
                : t("scans.column.duration")
            }
          >
            <ScanDuration {...scan} />
          </SummaryField>
          <SummaryField label={t("scans.column.trigger")}>
            {triggerText(t, scan)}
          </SummaryField>
          <SummaryField label={t("scans.form.accounts")}>
            {accounts}
          </SummaryField>
          <SummaryField label={t("scans.form.regions")}>{regions}</SummaryField>
          <SummaryField label={t("scans.form.types")}>
            {resourceTypes}
          </SummaryField>
          <ResourceMetrics counts={scan.counts.resources} />
        </dl>
      </section>

      <Section
        title={t("scans.detail.tasks")}
        actions={
          <span className="text-xs text-muted-foreground">
            {TASK_STATUSES.filter((status) => scan.counts.tasks[status])
              .map(
                (status) =>
                  `${t(`tasks.status.${status}` as MessageKey)} ${scan.counts.tasks[status].toLocaleString()}`,
              )
              .join(t("common.separator"))}
          </span>
        }
      >
        <TaskList scanId={scan.id} />
      </Section>
    </>
  )
}

function TaskList({ scanId }: { scanId: number }) {
  const { t } = useI18n()
  const types = useResourceTypes()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const params: Record<string, string> = {}
  for (const key of ["status", "kind", "accountId", "errorCode"]) {
    const value = first(state.filters[key])
    if (value) params[key] = value
  }
  const query = useScanTasks(scanId, params, {
    page: state.page,
    pageSize: state.pageSize,
  })
  const columns = useMemo<Column[]>(
    () =>
      [
        ["id", t("tasks.column.id" as MessageKey)],
        ["status", t("tasks.column.status")],
        ["kind", t("tasks.column.scope" as MessageKey)],
        ["accountId", t("tasks.column.account")],
        ["region", t("tasks.column.region")],
        ["resourceType", t("scans.form.types")],
        ["attempt", t("tasks.column.attempt")],
        ["startedAt", t("tasks.startedAt")],
        ["duration", t("scans.column.duration")],
        ["result", t("scans.column.result")],
      ].map(([key, label]) => ({
        key,
        label,
        type: "text",
        mono: ["id", "accountId", "region"].includes(key),
        sortable: false,
        filterable: false,
        scope: "common",
      })),
    [t],
  )
  const facets = useMemo<Record<string, Option[]>>(
    () => ({
      kind: TASK_KINDS.map((value) => ({
        value,
        label: t(`tasks.kind.${value}` as MessageKey),
      })),
      status: TASK_STATUSES.map((value) => ({
        value,
        label: t(`tasks.status.${value}` as MessageKey),
      })),
      errorCode: ERROR_CODES.map((value) => ({
        value,
        label: reasonText(t, value).title,
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
            #{row.id}
          </Link>
        )
      case "kind":
        return <>{t(`tasks.kind.${row.kind}` as MessageKey)}</>
      case "status":
        return (
          <span className={row.status === "failed" ? "text-warn" : undefined}>
            {t(`tasks.status.${row.status}` as MessageKey)}
          </span>
        )
      case "accountId":
        return (
          <Link
            to="/accounts/$accountId"
            params={{ accountId: row.accountId }}
            className="hover:text-primary hover:underline"
          >
            {row.accountId}
          </Link>
        )
      case "region":
        return <>{row.region ?? t("scans.scope.allRegions")}</>
      case "attempt":
        return (
          <>
            {row.startedAt
              ? t("tasks.attempt", { n: row.attempt, max: row.maxAttempts })
              : "—"}
          </>
        )
      case "resourceType": {
        const label = row.resourceType
          ? (types.data?.find((type) => type.typeName === row.resourceType)
              ?.label ?? row.resourceType)
          : row.kind === "config_group"
            ? t("tasks.configTypes" as MessageKey)
            : t("scans.scope.allTypes")
        return (
          <span className="block truncate" title={row.resourceType ?? label}>
            {label}
          </span>
        )
      }
      case "startedAt":
        return row.startedAt ? <Time value={row.startedAt} /> : <>—</>
      case "duration":
        return row.startedAt ? <ScanDuration {...row} /> : <>—</>
      case "result":
        return <TaskResult task={row} />
    }
    return undefined
  }

  function setFilter(key: string, value: string | undefined) {
    const filters = { ...state.filters }
    if (value) filters[key] = [value]
    else delete filters[key]
    setState({ ...state, filters, page: 1 })
  }

  const labels: Record<string, string> = {
    status: t("tasks.column.status"),
    kind: t("tasks.column.scope" as MessageKey),
    errorCode: t("scans.detail.failuresByReason"),
  }
  const filter = (key: string) => (
    <select
      key={key}
      value={params[key] ?? ""}
      onChange={(event) => setFilter(key, event.target.value || undefined)}
      aria-label={labels[key]}
      className={cn(selectClass, params[key] && selectActive)}
    >
      <option value="">{labels[key]}</option>
      {facets[key].map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )

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
          id: 100,
          status: 90,
          kind: 130,
          accountId: 150,
          region: 130,
          resourceType: 180,
          attempt: 80,
          startedAt: 130,
          duration: 110,
          result: 200,
        }}
        columnMenu={false}
        filterPopovers={false}
        toolbar={
          <search
            className="flex flex-wrap items-center gap-2"
            aria-label={t("scans.detail.tasks")}
          >
            {filter("status")}
            <Input
              value={params.accountId ?? ""}
              onChange={(event) =>
                setFilter("accountId", event.target.value || undefined)
              }
              aria-label={t("tasks.column.account")}
              placeholder={t("tasks.filter.account" as MessageKey)}
              className="h-8 w-48 max-w-full"
            />
            {filter("kind")}
            {filter("errorCode")}
            {Object.keys(params).length > 0 && (
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
