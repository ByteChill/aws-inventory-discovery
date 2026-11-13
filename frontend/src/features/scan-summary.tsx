import { Link } from "@tanstack/react-router"
import type { ReactNode } from "react"

import type { ScanSummary } from "@/client"
import { Time } from "@/components/time"
import { reasonText } from "@/features/scan-parts"
import { type MessageKey, useI18n } from "@/i18n"
import { formatDuration, formatNumber } from "@/lib/format"
import { cn } from "@/lib/utils"

function finishTime(value: string, locale: string) {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value))
}

const cardClass =
  "flex min-h-[156px] min-w-0 flex-col rounded-xl border bg-card px-[18px] py-4"
const linkClass =
  "transition-colors hover:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"

function Stat({ value, unit }: { value: ReactNode; unit?: string }) {
  return (
    <p className="mb-2.5 text-lg leading-tight font-semibold tracking-tight tabular-nums">
      {value}
      {unit && (
        <span className="ml-1.5 text-xs font-normal tracking-normal text-muted-foreground">
          {unit}
        </span>
      )}
    </p>
  )
}

function Label({ children }: { children: ReactNode }) {
  return (
    <h2 className="mb-2.5 text-xs font-semibold text-muted-foreground">
      {children}
    </h2>
  )
}

export function ScanSummaryCards({
  data,
  isLoading,
}: {
  data?: ScanSummary
  isLoading: boolean
}) {
  const { t, locale } = useI18n()
  const sweep = data?.sweep
  const unavailable = (
    <p className="text-sm text-muted-foreground">
      {isLoading ? t("common.loading") : t("scans.summary.unavailable")}
    </p>
  )
  const sweepBody = (
    <>
      <Label>{t("scans.summary.sweep")}</Label>
      {!data ? (
        unavailable
      ) : sweep ? (
        <>
          <Stat
            value={formatNumber(sweep.accountsDone, locale)}
            unit={t("scans.summary.accountsDone", {
              total: sweep.accountsTotal,
            })}
          />
          <p className="text-xs text-muted-foreground">
            {t("scans.summary.sweepLine", { id: sweep.scanRequestId })}
            <Time value={sweep.startedAt} />
          </p>
          {sweep.estimatedFinishAt && (
            <p className="mt-auto pt-2 text-xs text-muted-foreground">
              {t("scans.summary.estimatedFinish" as MessageKey, {
                time: finishTime(sweep.estimatedFinishAt, locale),
              })}
            </p>
          )}
          {sweep.behindSchedule && (
            <p className="mt-auto pt-2 text-xs text-warn">
              {t("scans.behindNoDuration")}
            </p>
          )}
        </>
      ) : (
        <Stat value="0" unit={t("scans.summary.sweepsRunning")} />
      )}
    </>
  )

  return (
    <section
      aria-label={t("scans.summary.label")}
      aria-busy={isLoading}
      className="grid grid-cols-[1.15fr_repeat(3,minmax(0,1fr))] gap-3.5 max-[1180px]:grid-cols-2 max-[800px]:grid-cols-1"
    >
      {sweep ? (
        <Link
          to="/scans/$id"
          params={{ id: String(sweep.scanRequestId) }}
          className={cn(
            cardClass,
            linkClass,
            "border-primary-soft-line bg-primary-soft",
          )}
        >
          {sweepBody}
        </Link>
      ) : (
        <article
          className={cn(cardClass, "border-primary-soft-line bg-primary-soft")}
        >
          {sweepBody}
        </article>
      )}
      <article className={cardClass}>
        <Label>{t("scans.summary.queued")}</Label>
        {!data ? (
          unavailable
        ) : (
          <>
            <Stat
              value={formatNumber(
                data.backlog.onDemand.queued + data.backlog.scheduled.queued,
                locale,
              )}
              unit={t("scans.summary.tasksUnit")}
            />
            {(
              [
                ["scans.backlogOnDemand", data.backlog.onDemand],
                ["scans.backlogScheduled", data.backlog.scheduled],
              ] as const
            ).map(([label, backlog]) => (
              <p key={label} className="text-xs text-muted-foreground">
                {t(label)}
                {t("common.separator")}
                {backlog.queued > 0
                  ? t("scans.backlogLine", {
                      count: backlog.queued,
                      wait: formatDuration(backlog.oldestWaitSeconds * 1000, t),
                    })
                  : t("scans.summary.noQueued")}
              </p>
            ))}
          </>
        )}
      </article>
      <article className={cardClass}>
        <Label>{t("scans.failures")}</Label>
        {!data ? (
          unavailable
        ) : (
          <>
            <Stat
              value={formatNumber(
                data.failures24h.reduce(
                  (sum, failure) => sum + failure.count,
                  0,
                ),
                locale,
              )}
              unit={t("scans.summary.tasksUnit")}
            />
            {data.failures24h.map((failure) => (
              <p
                key={failure.errorCode}
                className="text-xs text-muted-foreground"
              >
                {reasonText(t, failure.errorCode).title}
                {t("common.separator")}
                {t("scans.failureTasks", { count: failure.count })}
              </p>
            ))}
          </>
        )}
      </article>
      <Link
        to="/resources"
        search={{ f: { status: ["missing"] } }}
        className={cn(cardClass, linkClass)}
      >
        <Label>{t("scans.missing")}</Label>
        {!data ? (
          unavailable
        ) : (
          <Stat
            value={formatNumber(data.missingCount, locale)}
            unit={t("scans.summary.resourcesUnit")}
          />
        )}
        {data && (
          <>
            <p className="text-xs text-muted-foreground">
              {t("scans.summary.missingHint")}
            </p>
            <p className="mt-auto pt-2 text-xs text-muted-foreground">
              {t("scans.summary.viewMissing")}
            </p>
          </>
        )}
      </Link>
    </section>
  )
}
