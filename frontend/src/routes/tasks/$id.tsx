import { createFileRoute, Link } from "@tanstack/react-router"
import { useState } from "react"

import { useResourceTypes } from "@/api/hooks"
import { useTask, useTaskLogs } from "@/api/scans"
import type { TaskOut } from "@/client"
import { InlineError, QueryRegion } from "@/components/inline-state"
import { Page, PageHeader } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  hasResourceCounts,
  ResourceMetrics,
  reasonText,
  ScanBreadcrumbs,
  ScanDuration,
  SummaryField,
} from "@/features/scan-parts"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"

export const Route = createFileRoute("/tasks/$id")({
  component: TaskPage,
})

function TaskPage() {
  const { t } = useI18n()
  const id = Number(Route.useParams().id)
  const query = useTask(id)
  return (
    <Page>
      <QueryRegion
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => query.refetch()}
        isEmpty={!query.data}
        emptyMessage={t("error.task_not_found")}
      >
        {query.data && <Detail task={query.data} />}
      </QueryRegion>
    </Page>
  )
}

function Detail({ task }: { task: TaskOut }) {
  const { t } = useI18n()
  const types = useResourceTypes()
  const reason = task.errorCode ? reasonText(t, task.errorCode) : null
  const typeLabel = task.resourceType
    ? (types.data?.find((type) => type.typeName === task.resourceType)?.label ??
      task.resourceType)
    : task.kind === "config_group"
      ? t("tasks.configTypes" as MessageKey)
      : t("scans.scope.allTypes")
  const subtitle = [
    t(`tasks.kind.${task.kind}` as MessageKey),
    task.accountId,
    task.region,
    typeLabel,
    task.resourceId,
  ]
    .filter(Boolean)
    .join(t("common.separator"))
  return (
    <>
      <div>
        <ScanBreadcrumbs scanId={task.scanRequestId} taskId={task.id} />
        <PageHeader
          title={
            <>
              {t("tasks.detail.title")}{" "}
              <span className="font-mono">#{task.id}</span>
            </>
          }
          description={
            <span className="[overflow-wrap:anywhere]">{subtitle}</span>
          }
          actions={
            <Button
              variant="outline"
              onClick={() => {
                void navigator.clipboard.writeText(location.href)
              }}
            >
              {t("action.copyLink")}
            </Button>
          }
        />
      </div>

      {(reason || task.errorMessage) && (
        <section className="rounded-xl border bg-subtle px-5 py-4">
          <h2>
            {reason
              ? `${t("tasks.status.failed")}：${reason.title}`
              : t("tasks.detail.error")}
          </h2>
          {reason?.hint && (
            <p className="mt-2 text-muted-foreground">{reason.hint}</p>
          )}
          {task.errorMessage && (
            <p className="mt-2 font-mono break-all text-muted-foreground">
              {task.errorMessage}
            </p>
          )}
        </section>
      )}

      <section className="rounded-xl border bg-background px-5 py-4">
        <h2 className="mb-3">{t("scans.detail.summary" as MessageKey)}</h2>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 min-[1180px]:grid-cols-4">
          <SummaryField label={t("tasks.column.status")}>
            <span
              className={task.status === "failed" ? "text-warn" : undefined}
            >
              {t(`tasks.status.${task.status}` as MessageKey)}
            </span>
          </SummaryField>
          <SummaryField label={t("tasks.scan")}>
            <Link
              to="/scans/$id"
              params={{ id: String(task.scanRequestId) }}
              className="font-mono text-primary hover:underline"
            >
              #{task.scanRequestId}
            </Link>
          </SummaryField>
          <SummaryField label={t("tasks.column.scope" as MessageKey)}>
            {t(`tasks.kind.${task.kind}` as MessageKey)}
          </SummaryField>
          <SummaryField label={t("tasks.source" as MessageKey)}>
            {task.source ? t(`source.${task.source}` as MessageKey) : "—"}
          </SummaryField>
          <SummaryField label={t("tasks.column.account")}>
            <Link
              to="/accounts/$accountId"
              params={{ accountId: task.accountId }}
              className="font-mono text-primary hover:underline"
            >
              {task.accountId}
            </Link>
          </SummaryField>
          <SummaryField label={t("tasks.column.region")}>
            <span className={task.region ? "font-mono" : undefined}>
              {task.region ?? t("scans.scope.allRegions")}
            </span>
          </SummaryField>
          <SummaryField label={t("scans.form.types")}>
            {typeLabel}
            {task.resourceId && (
              <span className="mt-1 block font-mono text-xs text-muted-foreground">
                {task.resourceId}
              </span>
            )}
          </SummaryField>
          <SummaryField label={t("tasks.column.attempt")}>
            {task.startedAt
              ? t("tasks.attempt", { n: task.attempt, max: task.maxAttempts })
              : t("scans.detail.notStarted" as MessageKey)}
          </SummaryField>
          <SummaryField label={t("tasks.createdAt")}>
            <Time value={task.createdAt} />
          </SummaryField>
          <SummaryField label={t("tasks.startedAt")}>
            {task.startedAt ? (
              <Time value={task.startedAt} />
            ) : (
              t("scans.detail.notStarted" as MessageKey)
            )}
          </SummaryField>
          <SummaryField label={t("tasks.column.finished")}>
            {task.finishedAt ? <Time value={task.finishedAt} /> : "—"}
          </SummaryField>
          <SummaryField
            label={
              task.status === "running"
                ? t("scans.detail.elapsed" as MessageKey)
                : t("scans.column.duration")
            }
          >
            <ScanDuration {...task} />
          </SummaryField>
        </dl>
      </section>

      {task.stats && hasResourceCounts(task.stats) && (
        <section className="rounded-xl border bg-background px-5 py-4">
          <h2 className="mb-3">
            {t(
              task.status === "failed"
                ? ("tasks.writtenBeforeFailure" as MessageKey)
                : "scans.column.result",
            )}
          </h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 min-[800px]:grid-cols-3 min-[1180px]:grid-cols-6">
            <ResourceMetrics counts={task.stats} includeUnchanged />
          </dl>
        </section>
      )}

      <section className="rounded-xl border bg-background px-5 py-4">
        <Logs id={task.id} />
      </section>
    </>
  )
}

function Logs({ id }: { id: number }) {
  const { t } = useI18n()
  const [search, setSearch] = useState("")
  const query = useTaskLogs(id)
  const events = query.data?.pages.flatMap((page) => page.events) ?? []
  const matching = events.filter((event) =>
    `${event.timestamp} ${event.level} ${event.message}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  )
  // Expected where there is no CloudWatch (local runs): one calm line.
  const unavailable =
    (query.error as { code?: string } | null)?.code === "dependency_unavailable"

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2>{t("tasks.detail.logs")}</h2>
        {!unavailable && (
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label={t("tasks.logsSearch" as MessageKey)}
              placeholder={t("tasks.logsSearch" as MessageKey)}
              className="h-8 w-56 max-w-full"
            />
            <Button
              variant="outline"
              disabled={!matching.length}
              onClick={() => {
                void navigator.clipboard.writeText(
                  matching
                    .map(
                      (event) =>
                        `${event.timestamp} ${event.level} ${event.message}`,
                    )
                    .join("\n"),
                )
              }}
            >
              {t("action.copy")}
            </Button>
          </div>
        )}
      </div>
      {unavailable ? (
        <p className="text-muted-foreground">{t("tasks.logsUnavailable")}</p>
      ) : (
        <QueryRegion
          isLoading={query.isLoading}
          error={events.length ? null : query.error}
          onRetry={() => query.refetch()}
          isEmpty={events.length === 0}
          emptyMessage={t("tasks.noLogs")}
        >
          <div className="flex flex-col gap-3">
            <section
              // biome-ignore lint/a11y/noNoninteractiveTabindex: keyboard users scroll the log viewport
              tabIndex={0}
              className="max-h-[32rem] overflow-auto rounded-lg bg-subtle p-4"
              aria-label={t("tasks.detail.logs")}
            >
              {matching.length === 0 ? (
                <p className="text-muted-foreground">
                  {t("tasks.logsNoMatch" as MessageKey)}
                </p>
              ) : (
                <ul className="flex flex-col gap-1 font-mono text-xs">
                  {matching.map((event, index) => (
                    <li
                      // biome-ignore lint/suspicious/noArrayIndexKey: log lines have no id
                      key={index}
                      className="flex flex-wrap items-baseline gap-x-3"
                    >
                      <Time
                        value={event.timestamp}
                        className="shrink-0 text-muted-foreground"
                      />
                      <span
                        className={
                          event.level.toLowerCase() === "error"
                            ? "shrink-0 text-danger"
                            : "shrink-0 text-muted-foreground"
                        }
                      >
                        {event.level}
                      </span>
                      <span className="min-w-0 flex-1 basis-56 break-all">
                        {event.message}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            {query.isFetchNextPageError && (
              <InlineError
                message={errorText(t, query.error)}
                onRetry={() => query.fetchNextPage()}
              />
            )}
            {query.hasNextPage && (
              <div>
                <Button
                  variant="outline"
                  disabled={query.isFetchingNextPage}
                  onClick={() => query.fetchNextPage()}
                >
                  {t("action.loadMore")}
                </Button>
              </div>
            )}
          </div>
        </QueryRegion>
      )}
    </>
  )
}
