import { Link } from "@tanstack/react-router"
import { Fragment } from "react"

import type { ScanDetail, ScanRequestOut } from "@/client"
import { type MessageKey, type TFunction, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatCount } from "@/lib/format"

type Scan = ScanRequestOut | ScanDetail

/** Trigger in words: schedule, a person at the dashboard, or the caller's name. */
export function triggerText(t: TFunction, scan: Scan): string {
  if (scan.trigger === "schedule") return t("scans.trigger.scheduled")
  if (!scan.callerId || scan.callerId === "dashboard")
    return t("scans.trigger.manual")
  return scan.callerId
}

/** "12 个账号，全部区域，全部资源类型", or the one resource of a refresh. */
export function scopeText(t: TFunction, scan: Scan): string {
  const s = scan.scope
  if (!s) {
    const id = scan.target?.resourceId
    return id ? t("scans.scope.resource", { id }) : ""
  }
  if (s.all) return t("scans.scope.allAccounts")
  return [
    s.accountIds?.length
      ? formatCount(t, "accounts", s.accountIds.length)
      : t("scans.scope.allAccounts"),
    s.awsRegions?.length
      ? formatCount(t, "regions", s.awsRegions.length)
      : t("scans.scope.allRegions"),
    s.resourceTypes?.length
      ? formatCount(t, "types", s.resourceTypes.length)
      : t("scans.scope.allTypes"),
  ].join(t("common.separator"))
}

export function scanStatusText(t: TFunction, status: string): string {
  const key = `scans.status.${status}` as MessageKey
  const text = t(key)
  return text === key ? status : text
}

/** Plain-language reason and what to do about it, from scanner.md section 9. */
export function reasonText(
  t: TFunction,
  code: string,
): { title: string; hint: string | null } {
  const titleKey = `scanError.${code}.title` as MessageKey
  const title = t(titleKey)
  if (title === titleKey) return { title: code, hint: null }
  return { title, hint: t(`scanError.${code}.hint` as MessageKey) }
}

/** One localized reason from a refresh error: a scan error code, an API error, or a failed request. */
export function refreshReason(t: TFunction, error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code
  if (typeof code === "string") {
    const reason = reasonText(t, code)
    if (reason.hint !== null) return reason.title
  }
  return errorText(t, error)
}

/**
 * The numbers of a scan. Each links to the resources it touched (filtered by
 * the scan's time range and status) or, for failures, to the failed tasks.
 */
export function ScanResult({ scan }: { scan: Scan }) {
  const { t } = useI18n()
  const r = scan.counts.resources
  const failed = scan.counts.tasks.failed ?? 0
  const range = {
    gte: scan.startedAt ?? scan.createdAt,
    ...(scan.finishedAt ? { lte: scan.finishedAt } : {}),
  }
  const scope = {
    ...(scan.scope?.accountIds?.length
      ? { accountId: scan.scope.accountIds }
      : {}),
    ...(scan.scope?.awsRegions?.length
      ? { awsRegion: scan.scope.awsRegions }
      : {}),
    ...(scan.scope?.resourceTypes?.length
      ? { resourceType: scan.scope.resourceTypes }
      : {}),
  }
  const parts: { key: string; node: React.ReactNode }[] = []
  const add = (
    key: string,
    label: MessageKey,
    count: number | undefined,
    f: Record<string, unknown>,
  ) => {
    if (!count) return
    parts.push({
      key,
      node: (
        <Link
          to="/resources"
          search={{ f: { ...scope, ...f } } as never}
          className="hover:text-primary hover:underline"
        >
          {t(label, { count })}
        </Link>
      ),
    })
  }
  add("new", "scans.result.added", r.new, { firstSeenAt: range })
  add("updated", "scans.result.updated", r.updated, { lastChangedAt: range })
  add("missing", "scans.result.missing", r.missing, {
    status: ["missing"],
    missingSince: range,
  })
  add("deleted", "scans.result.deleted", r.deleted, {
    status: ["deleted"],
    deletedAt: range,
  })
  if (failed)
    parts.push({
      key: "failed",
      node: (
        <Link
          to="/scans/$id"
          params={{ id: String(scan.id) }}
          search={{ f: { status: ["failed"] } } as never}
          className="hover:text-primary hover:underline"
        >
          {t("scans.result.failed", { count: failed })}
        </Link>
      ),
    })
  if (parts.length === 0)
    return (
      <span className="text-muted-foreground">{t("scans.result.none")}</span>
    )
  return (
    <>
      {parts.map((p, i) => (
        <Fragment key={p.key}>
          {i > 0 && t("common.separator")}
          {p.node}
        </Fragment>
      ))}
    </>
  )
}
