import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { type ReactNode, useMemo, useState } from "react"
import { useResourceTypes } from "@/api/hooks"
import {
  useAbnormalScopes,
  useConfirmChange,
  useCreateScan,
  useMissingResources,
  useRefreshResource,
  useScanSummary,
  useScans,
} from "@/api/scans"
import type { ResourceItem, ScanRequestOut, ScanScopeOut } from "@/client"
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
import { Dialog } from "@/components/ui/dialog"
import { useScanColumns } from "@/features/columns"
import { ScanDialog } from "@/features/scan-dialog"
import {
  reasonText,
  refreshReason,
  ScanResult,
  scanStatusText,
  scopeText,
  triggerText,
} from "@/features/scan-parts"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatDate, formatDuration, formatNumber } from "@/lib/format"

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

/** 16:40 today, or the date and time for another day. */
function clock(value: string): string {
  const d = new Date(value)
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
  return d.toDateString() === new Date().toDateString()
    ? time
    : `${formatDate(d)} ${time}`
}

function Scans() {
  const { t } = useI18n()
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
      case "createdAt":
        return (
          <Link
            to="/scans/$id"
            params={{ id: String(row.id) }}
            className="text-primary hover:underline"
          >
            <Time value={row.startedAt ?? row.createdAt} />
          </Link>
        )
      case "trigger":
        return <>{triggerText(t, row)}</>
      case "scope":
        return <>{scopeText(t, row)}</>
      case "status":
        return <>{scanStatusText(t, row.status)}</>
      case "result":
        return <ScanResult scan={row} />
    }
    return undefined
  }

  const s = summary.data
  // Failures link to a task list; the newest scan is where they most likely are.
  const failureScan = s?.sweep?.scanRequestId ?? scans.data?.items[0]?.id

  const blocks: ReactNode[] = []
  if (s?.sweep) {
    const sw = s.sweep
    const pct = sw.accountsTotal
      ? Math.round((sw.accountsDone / sw.accountsTotal) * 100)
      : 0
    blocks.push(
      <Section key="sweep" title={t("scans.progress")}>
        <div className="flex flex-col gap-2">
          <p>
            <Link
              to="/scans/$id"
              params={{ id: String(sw.scanRequestId) }}
              className="hover:text-primary hover:underline"
            >
              {sw.estimatedFinishAt
                ? t("scans.progressLine", {
                    done: sw.accountsDone,
                    total: sw.accountsTotal,
                    time: clock(sw.estimatedFinishAt),
                  })
                : t("scans.progressNoEta", {
                    done: sw.accountsDone,
                    total: sw.accountsTotal,
                  })}
            </Link>
            {sw.behindSchedule && (
              <span className="ml-3 text-warn">
                {t("scans.behindNoDuration")}
              </span>
            )}
          </p>
          <div
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            className="h-1.5 max-w-md overflow-hidden rounded-sm bg-subtle-strong"
          >
            <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
          </div>
        </div>
      </Section>,
    )
  }
  if (s && (s.backlog.onDemand.queued > 0 || s.backlog.scheduled.queued > 0)) {
    const rows = [
      ["scans.backlogOnDemand", s.backlog.onDemand],
      ["scans.backlogScheduled", s.backlog.scheduled],
    ] as const
    blocks.push(
      <Section key="backlog" title={t("scans.backlog")}>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5">
          {rows
            .filter(([, b]) => b.queued > 0)
            .map(([label, b]) => (
              <div key={label} className="contents">
                <dt className="text-muted-foreground">
                  {t(label as MessageKey)}
                </dt>
                <dd>
                  {t("scans.backlogLine", {
                    count: b.queued,
                    wait: formatDuration(b.oldestWaitSeconds * 1000, t),
                  })}
                </dd>
              </div>
            ))}
        </dl>
      </Section>,
    )
  }
  if (s && s.failures24h.length > 0) {
    blocks.push(
      <Section key="failures" title={t("scans.failures")}>
        <ul className="flex flex-col gap-3">
          {s.failures24h.map((f) => {
            const reason = reasonText(t, f.errorCode)
            const body = (
              <>
                <span className="font-medium">{reason.title}</span>
                <span className="ml-3 text-muted-foreground">
                  {t("scans.failureTasks", { count: f.count })}
                </span>
                {reason.hint && (
                  <span className="block text-muted-foreground">
                    {reason.hint}
                  </span>
                )}
              </>
            )
            return (
              <li key={f.errorCode}>
                {failureScan ? (
                  <Link
                    to="/scans/$id"
                    params={{ id: String(failureScan) }}
                    search={{ f: { errorCode: [f.errorCode] } } as never}
                    className="block hover:text-primary"
                  >
                    {body}
                  </Link>
                ) : (
                  body
                )}
              </li>
            )
          })}
        </ul>
      </Section>,
    )
  }
  if (s && s.missingCount > 0)
    blocks.push(<MissingBlock key="missing" count={s.missingCount} />)
  if (s && s.abnormalScopeCount > 0)
    blocks.push(<AnomalyBlock key="anomalies" />)

  return (
    <Page>
      <PageHeader
        title={t("scans.title")}
        actions={
          <Button onClick={() => setFormOpen(true)}>{t("action.scan")}</Button>
        }
      />
      {summary.error && (
        <InlineError
          message={errorText(t, summary.error)}
          onRetry={() => summary.refetch()}
        />
      )}
      {blocks}
      <Section title={t("scans.history")}>
        <div className="h-[34rem]">
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
            renderCell={renderCell}
          />
        </div>
      </Section>
      <ScanDialog open={formOpen} onClose={() => setFormOpen(false)} />
    </Page>
  )
}

function MissingBlock({ count }: { count: number }) {
  const { t, locale } = useI18n()
  const query = useMissingResources(true)
  const types = useResourceTypes()
  const labels = useMemo(
    () => new Map(types.data?.map((x) => [x.typeName, x.label])),
    [types.data],
  )
  return (
    <Section
      title={`${t("scans.missing")}${t("common.separator")}${formatNumber(count, locale)}`}
    >
      {query.error && (
        <InlineError
          message={errorText(t, query.error)}
          onRetry={() => query.refetch()}
        />
      )}
      <ul className="flex flex-col">
        {query.data?.items.map((r) => (
          <MissingRow
            key={r.id}
            resource={r}
            typeLabel={labels.get(r.resourceType) ?? r.resourceType}
          />
        ))}
      </ul>
      {query.data && query.data.total > query.data.items.length && (
        <div>
          <Button asChild variant="link" className="px-0">
            <Link
              to="/resources"
              search={{ f: { status: ["missing"] } } as never}
            >
              {t("table.total.resources", { count: query.data.total })}
            </Link>
          </Button>
        </div>
      )}
    </Section>
  )
}

function MissingRow({
  resource: r,
  typeLabel,
}: {
  resource: ResourceItem
  typeLabel: string
}) {
  const { t } = useI18n()
  const refresh = useRefreshResource()
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b py-1.5">
      <Link
        to="/resources/$id"
        params={{ id: String(r.id) }}
        className="min-w-0 break-all text-primary hover:underline"
      >
        {r.resourceName ?? r.resourceId}
      </Link>
      <span className="text-muted-foreground">{typeLabel}</span>
      <span className="text-muted-foreground">{r.account.accountName}</span>
      <span className="font-mono text-muted-foreground">{r.awsRegion}</span>
      {r.missingSince && (
        <span className="text-muted-foreground">
          <Time value={r.missingSince} />
        </span>
      )}
      <div className="ml-auto flex items-center gap-3">
        {refresh.error != null && (
          <span className="text-danger">{refreshReason(t, refresh.error)}</span>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={refresh.phase !== "idle"}
          onClick={() =>
            refresh.run({
              accountId: r.accountId,
              awsRegion: r.awsRegion,
              resourceType: r.resourceType,
              resourceId: r.resourceId,
            })
          }
        >
          {refresh.phase === "queued"
            ? t("detail.refreshing")
            : t("action.recheck")}
        </Button>
      </div>
    </li>
  )
}

function AnomalyBlock() {
  const { t } = useI18n()
  const query = useAbnormalScopes()
  const types = useResourceTypes()
  const labels = useMemo(
    () => new Map(types.data?.map((x) => [x.typeName, x.label])),
    [types.data],
  )
  const [confirming, setConfirming] = useState<ScanScopeOut | null>(null)
  const confirm = useConfirmChange()
  const rescan = useRescan()

  return (
    <Section title={t("scans.anomalies")}>
      {query.error && (
        <InlineError
          message={errorText(t, query.error)}
          onRetry={() => query.refetch()}
        />
      )}
      {rescan.error && <InlineError message={errorText(t, rescan.error)} />}
      <ul className="flex flex-col">
        {query.data?.items.map((s) => (
          <li
            key={`${s.accountId}|${s.region}|${s.resourceType}`}
            className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b py-1.5"
          >
            <span className="font-mono">{s.accountId}</span>
            <span className="font-mono text-muted-foreground">{s.region}</span>
            <span>{labels.get(s.resourceType) ?? s.resourceType}</span>
            <span className="text-muted-foreground">
              {t("scans.anomalyRow", {
                active: s.activeCount,
                candidates: s.candidateCount,
              })}
            </span>
            <div className="ml-auto flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={rescan.isPending}
                onClick={() => rescan.mutate(s)}
              >
                {t("action.rescan")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirming(s)}
              >
                {t("action.confirmChange")}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <Dialog
        open={confirming !== null}
        onClose={() => setConfirming(null)}
        title={t("action.confirmChange")}
      >
        <p>{t("scans.anomalyConfirm")}</p>
        {confirm.error && <InlineError message={errorText(t, confirm.error)} />}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setConfirming(null)}>
            {t("action.cancel")}
          </Button>
          <Button
            disabled={confirm.isPending}
            onClick={() =>
              confirming &&
              confirm.mutate(
                {
                  accountId: confirming.accountId,
                  awsRegion: confirming.region,
                  resourceType: confirming.resourceType,
                },
                { onSuccess: () => setConfirming(null) },
              )
            }
          >
            {t("action.confirm")}
          </Button>
        </div>
      </Dialog>
    </Section>
  )
}

/** Rescan one abnormal scope, then go to that scan. */
function useRescan() {
  const create = useCreateScan()
  const navigate = useNavigate()
  return {
    isPending: create.isPending,
    error: create.error,
    mutate: (s: ScanScopeOut) =>
      create.mutate(
        {
          spec: {
            accountIds: [s.accountId],
            awsRegions: [s.region],
            resourceTypes: [s.resourceType],
          },
        },
        {
          onSuccess: (scan) =>
            navigate({
              to: "/scans/$id",
              params: { id: String(scan.id) },
            }),
        },
      ),
  }
}
