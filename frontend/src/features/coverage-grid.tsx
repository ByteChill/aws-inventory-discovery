import { useMemo } from "react"

import type { CoverageCell } from "@/client"
import { useI18n } from "@/i18n"
import { formatExactTime } from "@/lib/format"
import { cn } from "@/lib/utils"

type State = CoverageCell["state"] | "never"

const STATES: State[] = ["ok", "failed", "access_denied", "stale", "never"]
const LABEL = {
  ok: "accounts.cell.ok",
  failed: "accounts.cell.failed",
  access_denied: "accounts.cell.denied",
  stale: "accounts.cell.stale",
  never: "accounts.cell.none",
} as const

// Status colors come from tokens; the legend names every one, so color is never the only signal.
const SWATCH: Record<State, string> = {
  ok: "bg-primary",
  failed: "bg-danger",
  access_denied: "bg-warn",
  stale: "border border-warn bg-warn-soft",
  never: "bg-subtle-strong",
}

const ERROR_TEXT = {
  AccessDeniedException: "scanError.access_denied.title",
  AccessDenied: "scanError.access_denied.title",
  Throttling: "scanError.throttled.title",
  ThrottlingException: "scanError.throttled.title",
} as const

/** Region x resource type grid: one square per scope, hover for time and reason. */
export function CoverageGrid({
  cells,
  typeLabels,
}: {
  cells: CoverageCell[]
  typeLabels: Map<string, string>
}) {
  const { t, locale } = useI18n()
  const { regions, types, byKey } = useMemo(() => {
    const regionSet = new Set<string>()
    const typeSet = new Set<string>()
    const map = new Map<string, CoverageCell>()
    for (const c of cells) {
      regionSet.add(c.region)
      typeSet.add(c.resourceType)
      map.set(`${c.resourceType}|${c.region}`, c)
    }
    return {
      regions: [...regionSet].sort(),
      types: [...typeSet].sort((a, b) =>
        (typeLabels.get(a) ?? a).localeCompare(typeLabels.get(b) ?? b, locale),
      ),
      byKey: map,
    }
  }, [cells, typeLabels, locale])

  function describeCell(cell: CoverageCell | undefined, state: State): string {
    const lines = [t(LABEL[state])]
    if (cell?.lastSucceededAt)
      lines.push(
        t("accounts.cell.lastOk", {
          time: formatExactTime(cell.lastSucceededAt, locale),
        }),
      )
    if (cell?.lastErrorCode && state !== "ok") {
      const key = ERROR_TEXT[cell.lastErrorCode as keyof typeof ERROR_TEXT]
      lines.push(
        t("accounts.cell.reason", {
          reason: key ? t(key) : cell.lastErrorCode,
        }),
      )
    }
    return lines.join("\n")
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-auto">
        <table className="border-separate border-spacing-0 text-left">
          <thead>
            <tr>
              <th className="border-b py-1.5 pr-6 font-medium">
                <span className="sr-only">{t("accounts.matrix")}</span>
              </th>
              {regions.map((r) => (
                <th
                  key={r}
                  scope="col"
                  className="border-b px-3 py-1.5 text-center font-mono font-normal"
                >
                  {r}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {types.map((type) => (
              <tr key={type}>
                <th
                  scope="row"
                  title={type}
                  className="border-b py-1.5 pr-6 font-normal"
                >
                  {typeLabels.get(type) ?? type}
                </th>
                {regions.map((region) => {
                  const cell = byKey.get(`${type}|${region}`)
                  const state: State = cell?.state ?? "never"
                  const text = describeCell(cell, state)
                  return (
                    <td
                      key={region}
                      title={text}
                      className="border-b px-3 py-1.5 text-center"
                    >
                      <span
                        role="img"
                        aria-label={text.replace("\n", ", ")}
                        className={cn(
                          "inline-block size-4 rounded-sm align-middle",
                          SWATCH[state],
                        )}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="flex flex-wrap gap-x-5 gap-y-1">
        {STATES.map((s) => (
          <li key={s} className="flex items-center gap-1.5">
            <span className={cn("size-3 rounded-sm", SWATCH[s])} />
            {t(LABEL[s])}
          </li>
        ))}
      </ul>
    </div>
  )
}
