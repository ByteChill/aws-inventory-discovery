import { createFileRoute, Link } from "@tanstack/react-router"
import type { ReactNode } from "react"

import { useTask, useTaskLogs } from "@/api/scans"
import type { TaskOut } from "@/client"
import { InlineError, QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { reasonText } from "@/features/scan-parts"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatDuration } from "@/lib/format"

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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  )
}

function Detail({ task }: { task: TaskOut }) {
  const { t } = useI18n()
  const reason = task.errorCode ? reasonText(t, task.errorCode) : null
  const took =
    task.startedAt && task.finishedAt
      ? new Date(task.finishedAt).getTime() - new Date(task.startedAt).getTime()
      : null
  return (
    <>
      <PageHeader
        title={
          <>
            {t("tasks.detail.title")}{" "}
            <span className="font-mono text-base text-muted-foreground">
              {task.id}
            </span>
          </>
        }
      />

      <Section title={t("tasks.detail.info")}>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5">
          <Field label={t("tasks.column.status")}>
            {t(`tasks.status.${task.status}` as MessageKey)}
          </Field>
          <Field label={t("tasks.column.kind")}>
            {t(`tasks.kind.${task.kind}` as MessageKey)}
          </Field>
          <Field label={t("tasks.scan")}>
            <Link
              to="/scans/$id"
              params={{ id: String(task.scanRequestId) }}
              className="font-mono text-primary hover:underline"
            >
              {task.scanRequestId}
            </Link>
          </Field>
          <Field label={t("tasks.column.account")}>
            <Link
              to="/accounts/$accountId"
              params={{ accountId: task.accountId }}
              className="font-mono text-primary hover:underline"
            >
              {task.accountId}
            </Link>
          </Field>
          {task.region && (
            <Field label={t("tasks.column.region")}>
              <span className="font-mono">{task.region}</span>
            </Field>
          )}
          {task.resourceType && (
            <Field label={t("tasks.column.resource")}>
              <span className="font-mono">{task.resourceType}</span>
              {task.resourceId && (
                <span className="ml-2 font-mono break-all">
                  {task.resourceId}
                </span>
              )}
            </Field>
          )}
          <Field label={t("tasks.column.attempt")}>
            {t("tasks.attempt", { n: task.attempt, max: task.maxAttempts })}
          </Field>
          <Field label={t("tasks.createdAt")}>
            <Time value={task.createdAt} />
          </Field>
          {task.startedAt && (
            <Field label={t("tasks.startedAt")}>
              <Time value={task.startedAt} />
            </Field>
          )}
          {task.finishedAt && (
            <Field label={t("tasks.column.finished")}>
              <Time value={task.finishedAt} />
            </Field>
          )}
          {took !== null && (
            <Field label={t("tasks.duration")}>{formatDuration(took, t)}</Field>
          )}
        </dl>
      </Section>

      {reason && (
        <Section title={t("tasks.detail.error")}>
          <div className="flex flex-col gap-1">
            <p className="font-medium">{reason.title}</p>
            {reason.hint && (
              <p className="text-muted-foreground">{reason.hint}</p>
            )}
            {task.errorMessage && (
              <p className="font-mono break-all text-muted-foreground">
                {task.errorMessage}
              </p>
            )}
          </div>
        </Section>
      )}

      <Section title={t("tasks.detail.logs")}>
        <Logs id={task.id} />
      </Section>
    </>
  )
}

function Logs({ id }: { id: number }) {
  const { t } = useI18n()
  const query = useTaskLogs(id)
  const events = query.data?.pages.flatMap((p) => p.events) ?? []
  // Expected where there is no CloudWatch (local runs): one calm line.
  const unavailable =
    (query.error as { code?: string } | null)?.code === "dependency_unavailable"

  if (unavailable)
    return <p className="text-muted-foreground">{t("tasks.logsUnavailable")}</p>

  return (
    <QueryRegion
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => query.refetch()}
      isEmpty={events.length === 0}
      emptyMessage={t("tasks.noLogs")}
    >
      <div className="flex flex-col gap-3">
        <ul className="flex flex-col font-mono text-sm">
          {events.map((e, i) => (
            <li
              // biome-ignore lint/suspicious/noArrayIndexKey: log lines have no id
              key={i}
              className="flex gap-3 border-b py-1"
            >
              <Time
                value={e.timestamp}
                className="shrink-0 text-muted-foreground"
              />
              <span
                className={
                  e.level.toLowerCase() === "error"
                    ? "shrink-0 text-danger"
                    : "shrink-0 text-muted-foreground"
                }
              >
                {e.level}
              </span>
              <span className="min-w-0 break-all">{e.message}</span>
            </li>
          ))}
        </ul>
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
  )
}
