import type { MessageKey, TFunction } from "@/i18n"
import type { Locale } from "@/lib/preferences"

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

export function formatNumber(n: number, locale: Locale): string {
  return new Intl.NumberFormat(locale).format(n)
}

/** "128,430 个资源" / "128,430 resources" */
export function formatCount(
  t: TFunction,
  unit:
    | "resources"
    | "accounts"
    | "regions"
    | "types"
    | "tasks"
    | "scans"
    | "items",
  count: number,
): string {
  return t(`unit.${unit}` as MessageKey, { count })
}

function toDate(value: string | number | Date): Date {
  return value instanceof Date ? value : new Date(value)
}

function pad(n: number) {
  return String(n).padStart(2, "0")
}

/** 2026-11-08 */
export function formatDate(value: string | number | Date): string {
  const d = toDate(value)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 2026-11-08 14:03:21 GMT+8, the same in every language so it is unambiguous. */
export function formatExactTime(
  value: string | number | Date,
  locale: Locale,
): string {
  const d = toDate(value)
  const tz =
    new Intl.DateTimeFormat(locale, { timeZoneName: "short" })
      .formatToParts(d)
      .find((p) => p.type === "timeZoneName")?.value ?? ""
  return `${formatDate(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())} ${tz}`.trim()
}

/** "3 分钟前"; older than a day it is the date. */
export function formatRelativeTime(
  value: string | number | Date,
  t: TFunction,
  now: number = Date.now(),
): string {
  const d = toDate(value)
  const diff = now - d.getTime()
  if (diff >= DAY) return formatDate(d)
  if (diff < MINUTE) return t("time.justNow")
  if (diff < HOUR)
    return t("time.minutesAgo", { count: Math.floor(diff / MINUTE) })
  return t("time.hoursAgo", { count: Math.floor(diff / HOUR) })
}

/** Length of time: "40 分钟", "2 小时 5 分钟". */
export function formatDuration(ms: number, t: TFunction): string {
  const minutes = Math.round(ms / MINUTE)
  if (ms < MINUTE)
    return t("time.seconds", { count: Math.max(1, Math.round(ms / 1000)) })
  if (minutes < 60) return t("time.minutes", { count: minutes })
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m === 0
    ? t("time.hours", { count: h })
    : `${t("time.hours", { count: h })} ${t("time.minutes", { count: m })}`
}
