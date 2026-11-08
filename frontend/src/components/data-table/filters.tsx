import { useEffect, useId, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useI18n } from "@/i18n"
import { formatNumber } from "@/lib/format"
import type { Locale } from "@/lib/preferences"
import { cn } from "@/lib/utils"
import type { Column, FacetBucket, FilterValue, Range } from "./types"

type Change = (value: FilterValue | undefined) => void

/** Filter control chosen from the column type (dashboard.md section 2). */
export function FilterControl({
  column,
  value,
  onChange,
  options,
}: {
  column: Column
  value: FilterValue | undefined
  onChange: Change
  options: FacetBucket[] | undefined
}) {
  switch (column.type) {
    case "text":
      return <TextFilter value={value} onChange={onChange} />
    case "enum":
      return (
        <OptionList
          options={options ?? []}
          selected={Array.isArray(value) ? value : []}
          onChange={(next) => onChange(next.length ? next : undefined)}
        />
      )
    case "number":
      return <RangeFilter kind="number" value={value} onChange={onChange} />
    case "datetime":
      return <RangeFilter kind="datetime" value={value} onChange={onChange} />
    case "bool":
      return <BoolFilter value={value} onChange={onChange} />
  }
}

function TextFilter({
  value,
  onChange,
}: {
  value: FilterValue | undefined
  onChange: Change
}) {
  const { t } = useI18n()
  const [draft, setDraft] = useState(typeof value === "string" ? value : "")
  const first = useRef(true)
  const latest = useRef(onChange)
  latest.current = onChange
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    const id = setTimeout(() => latest.current(draft.trim() || undefined), 350)
    return () => clearTimeout(id)
  }, [draft])
  return (
    <Input
      value={draft}
      aria-label={t("filter.contains")}
      onChange={(e) => setDraft(e.target.value)}
      placeholder={t("filter.contains")}
    />
  )
}

/** Multi-select with a count after each option. */
export function OptionList({
  options,
  selected,
  onChange,
  className,
}: {
  options: FacetBucket[]
  selected: string[]
  onChange: (next: string[]) => void
  className?: string
}) {
  const { t, locale } = useI18n()
  const [query, setQuery] = useState("")
  const q = query.trim().toLowerCase()
  const visible = q
    ? options.filter((o) => `${o.label} ${o.value}`.toLowerCase().includes(q))
    : options

  if (options.length === 0) {
    return (
      <p className="px-1 py-1 text-muted-foreground">{t("filter.noOptions")}</p>
    )
  }

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      {options.length > 8 && (
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("filter.searchOptions")}
          aria-label={t("filter.searchOptions")}
        />
      )}
      <ul className="flex max-h-64 flex-col overflow-auto">
        {visible.map((o) => {
          const checked = selected.includes(o.value)
          return (
            <li key={o.value}>
              <label className="flex cursor-pointer items-start gap-2 rounded-sm px-1 py-1 hover:bg-subtle-strong">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    onChange(
                      checked
                        ? selected.filter((v) => v !== o.value)
                        : [...selected, o.value],
                    )
                  }
                  className="mt-1 accent-[var(--accent)]"
                />
                <span className="min-w-0 flex-1 break-words">{o.label}</span>
                <span className="text-muted-foreground">
                  {formatNumber(o.count, locale)}
                </span>
              </label>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function toLocalInput(iso: string | number | undefined): string {
  if (iso === undefined) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function RangeFilter({
  kind,
  value,
  onChange,
}: {
  kind: "number" | "datetime"
  value: FilterValue | undefined
  onChange: Change
}) {
  const { t } = useI18n()
  const range: Range<string | number> =
    value && typeof value === "object" && !Array.isArray(value) ? value : {}
  const [lo, setLo] = useState(
    kind === "datetime" ? toLocalInput(range.gte) : String(range.gte ?? ""),
  )
  const [hi, setHi] = useState(
    kind === "datetime" ? toLocalInput(range.lte) : String(range.lte ?? ""),
  )

  function commit() {
    const parse = (s: string): string | number | undefined => {
      if (!s) return undefined
      if (kind === "number") {
        const n = Number(s)
        return Number.isFinite(n) ? n : undefined
      }
      const d = new Date(s)
      return Number.isNaN(d.getTime()) ? undefined : d.toISOString()
    }
    const gte = parse(lo)
    const lte = parse(hi)
    onChange(gte === undefined && lte === undefined ? undefined : { gte, lte })
  }

  const uid = useId()
  const inputType = kind === "number" ? "number" : "datetime-local"
  const lowLabel = t(kind === "number" ? "filter.min" : "filter.from")
  const highLabel = t(kind === "number" ? "filter.max" : "filter.to")
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") commit()
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-1">
        <label htmlFor={`${uid}-lo`} className="text-xs text-muted-foreground">
          {lowLabel}
        </label>
        <Input
          id={`${uid}-lo`}
          type={inputType}
          value={lo}
          onChange={(e) => setLo(e.target.value)}
          onBlur={commit}
          onKeyDown={onKey}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={`${uid}-hi`} className="text-xs text-muted-foreground">
          {highLabel}
        </label>
        <Input
          id={`${uid}-hi`}
          type={inputType}
          value={hi}
          onChange={(e) => setHi(e.target.value)}
          onBlur={commit}
          onKeyDown={onKey}
        />
      </div>
    </div>
  )
}

function BoolFilter({
  value,
  onChange,
}: {
  value: FilterValue | undefined
  onChange: Change
}) {
  const { t } = useI18n()
  const items: { v: string | undefined; label: string }[] = [
    { v: undefined, label: t("common.all") },
    { v: "true", label: t("common.yes") },
    { v: "false", label: t("common.no") },
  ]
  return (
    <div role="radiogroup" className="flex gap-1">
      {items.map((it) => {
        const active = (value ?? undefined) === it.v
        return (
          <Button
            key={it.label}
            role="radio"
            aria-checked={active}
            variant={active ? "default" : "outline"}
            size="sm"
            className="flex-1"
            onClick={() => onChange(it.v)}
          >
            {it.label}
          </Button>
        )
      })}
    </div>
  )
}

/** Short human text for an active filter, shown in the filter chips. */
export function describeFilter(
  column: Pick<Column, "type" | "label"> | undefined,
  value: FilterValue,
  options: FacetBucket[] | undefined,
  t: ReturnType<typeof useI18n>["t"],
  locale: Locale,
): string {
  if (typeof value === "string") {
    if (column?.type === "bool")
      return value === "true" ? t("common.yes") : t("common.no")
    return value
  }
  if (Array.isArray(value)) {
    return value
      .map((v) => options?.find((o) => o.value === v)?.label ?? v)
      .join(t("common.separator"))
  }
  const fmt = (x: string | number | undefined) =>
    x === undefined
      ? ""
      : column?.type === "datetime"
        ? toLocalInput(x).replace("T", " ")
        : formatNumber(Number(x), locale)
  const isDate = column?.type === "datetime"
  if (value.gte !== undefined && value.lte !== undefined)
    return t("filter.range", { from: fmt(value.gte), to: fmt(value.lte) })
  if (value.gte !== undefined)
    return t(isDate ? "filter.gte" : "filter.gteNumber", {
      value: fmt(value.gte),
    })
  return t(isDate ? "filter.lte" : "filter.lteNumber", {
    value: fmt(value.lte),
  })
}
