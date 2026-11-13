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
 * The numbers of a scan. They are statistics only: the inventory keeps current
 * state, so there is no list of what changed in one scan (DESIGN D-07). Only
 * the failure number links, to this scan's failed tasks.
 */
export function ScanResult({ scan }: { scan: Scan }) {
  const { t } = useI18n()
  const r = scan.counts.resources
  const failed = scan.counts.tasks.failed ?? 0
  const parts: { key: string; label: MessageKey; count: number }[] = []
  const add = (key: string, label: MessageKey, count: number | undefined) => {
    if (count) parts.push({ key, label, count })
  }
  add("new", "scans.result.added", r.new)
  add("updated", "scans.result.updated", r.updated)
  add("missing", "scans.result.missing", r.missing)
  add("deleted", "scans.result.deleted", r.deleted)

  if (parts.length === 0 && !failed)
    return (
      <span className="text-muted-foreground">{t("scans.result.none")}</span>
    )
  return (
    <>
      {parts.map((p, i) => (
        <Fragment key={p.key}>
          {i > 0 && t("common.separator")}
          {t(p.label, { count: p.count })}
        </Fragment>
      ))}
      {failed > 0 && (
        <Fragment key="failed">
          {parts.length > 0 && t("common.separator")}
          <Link
            to="/scans/$id"
            params={{ id: String(scan.id) }}
            search={{ f: { status: ["failed"] } } as never}
            className="hover:text-primary hover:underline"
          >
            {t("scans.result.failed", { count: failed })}
          </Link>
        </Fragment>
      )}
    </>
  )
}

/** The current inventory inside this scan's scope, not what changed in it. */
export function CurrentScopeResources({ scan }: { scan: Scan }) {
  const { t } = useI18n()
  return (
    <Link
      to="/resources"
      search={{ scanRequestId: scan.id }}
      className="text-primary hover:underline"
    >
      {t("scans.viewScopeResources")}
    </Link>
  )
}
