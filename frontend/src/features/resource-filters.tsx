import { Download, ListFilter, Search } from "lucide-react"
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react"

import type {
  Column,
  FilterValue,
  Option,
  TableState,
} from "@/components/data-table"
import { FilterControl, OptionList } from "@/components/data-table/filters"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { TagFilter, type TagFilters } from "@/features/tag-filter"
import { useI18n } from "@/i18n"
import { singleType } from "@/lib/resource-query"
import type { Scope } from "@/lib/scope"
import { cn } from "@/lib/utils"

const selectClass =
  "h-9 max-w-[180px] flex-1 rounded-md border border-input bg-card px-2 text-foreground min-[801px]:flex-none"
const selectActive = "border-primary bg-primary-soft font-semibold text-primary"

const ALL_STATUSES = ["active", "missing", "deleted"]

/**
 * The resource list's filter row (dashboard.md 3.3): search, environment, region,
 * type and inventory status, with the identity and type-specific filters folded
 * into "More filters". The scope hides the filter it already fixes.
 */
export function ResourceFilters({
  state,
  onStateChange,
  facets,
  columns,
  scope,
  total,
  hideType,
  exportHref,
}: {
  state: TableState
  onStateChange: (next: TableState) => void
  facets: Record<string, Option[]>
  columns: Column[]
  scope: Scope | null
  total: number
  /** Service and group pages switch types with their own tabs. */
  hideType?: boolean
  exportHref: (format: "csv" | "jsonl") => string
}) {
  const { t } = useI18n()
  const [draft, setDraft] = useState(state.q ?? "")
  const first = useRef(true)

  // Typing settles into the URL after a pause; Enter applies it at once.
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    const id = setTimeout(() => {
      if ((state.q ?? "") === draft.trim()) return
      onStateChange({ ...state, q: draft.trim() || undefined, page: 1 })
    }, 350)
    return () => clearTimeout(id)
  }, [draft, state, onStateChange])

  useEffect(() => {
    setDraft(state.q ?? "")
  }, [state.q])

  const type = singleType(state)
  const typeColumns = useMemo(
    () => (type ? columns.filter((c) => c.scope === "type") : []),
    [columns, type],
  )

  function setFilters(patch: Record<string, FilterValue | undefined>) {
    const filters = { ...state.filters }
    for (const [key, value] of Object.entries(patch)) {
      if (value === undefined) delete filters[key]
      else filters[key] = value
    }
    onStateChange({ ...state, filters, page: 1 })
  }

  const single = (key: string) => {
    const value = state.filters[key]
    return typeof value === "string"
      ? value
      : Array.isArray(value)
        ? value[0]
        : ""
  }

  const env = scope?.kind === "acct" ? "" : single("accountType")
  const region = single("awsRegion")
  const selectedType = single("resourceType")
  const status = statusValue(state.filters.status)

  const moreKeys = [
    ...(scope?.kind === "acct" ? [] : ["accountId"]),
    ...(scope?.kind === "app" ? [] : ["eimId"]),
    "baId",
    "tag",
    "tagMissing",
    ...typeColumns.map((c) => c.key),
  ]
  const moreCount = moreKeys.filter(
    (key) => state.filters[key] !== undefined,
  ).length
  const hasFilters =
    !!state.q ||
    Object.keys(state.filters).some(
      (key) => !(hideType && key === "resourceType"),
    )

  function clearAll() {
    setDraft("")
    onStateChange({
      ...state,
      filters: hideType
        ? Object.fromEntries(
            Object.entries(state.filters).filter(
              ([key]) => key === "resourceType",
            ),
          )
        : {},
      q: undefined,
      page: 1,
    })
  }

  return (
    <search
      className="flex flex-wrap items-center gap-2"
      aria-label={t("resources.filter.searchLabel")}
    >
      <label className="flex h-9 min-w-[200px] flex-1 basis-[220px] items-center gap-2 rounded-md border border-input bg-card px-2.5 text-muted-foreground focus-within:outline-2 focus-within:outline-primary focus-within:outline-offset-1 sm:max-w-[320px]">
        <Search aria-hidden="true" className="size-4 shrink-0" />
        <input
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return
            e.preventDefault()
            onStateChange({
              ...state,
              q: draft.trim() || undefined,
              page: 1,
            })
          }}
          placeholder={t("resources.filter.searchPlaceholder")}
          aria-label={t("resources.filter.searchPlaceholder")}
          className="h-full min-w-0 flex-1 bg-transparent text-foreground placeholder:text-muted-foreground focus-visible:outline-0"
        />
      </label>

      {scope?.kind !== "acct" && (
        <select
          value={env}
          onChange={(e) =>
            setFilters({
              accountType: e.target.value ? [e.target.value] : undefined,
            })
          }
          aria-label={t("resources.group.accountType")}
          className={cn(selectClass, env && selectActive)}
        >
          <option value="">{t("resources.group.accountType")}</option>
          {(facets.accountType ?? []).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}

      <select
        value={region}
        onChange={(e) =>
          setFilters({
            awsRegion: e.target.value ? [e.target.value] : undefined,
          })
        }
        aria-label={t("resources.group.awsRegion")}
        className={cn(selectClass, region && selectActive)}
      >
        <option value="">{t("resources.group.awsRegion")}</option>
        {(facets.awsRegion ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>

      {!hideType && (
        <select
          value={selectedType}
          onChange={(e) =>
            setFilters({
              resourceType: e.target.value ? [e.target.value] : undefined,
            })
          }
          aria-label={t("resources.group.resourceType")}
          className={cn(selectClass, selectedType && selectActive)}
        >
          <option value="">{t("resources.group.resourceType")}</option>
          {(facets.resourceType ?? []).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}

      <select
        value={status}
        onChange={(e) => {
          const value = e.target.value
          setFilters({
            status:
              value === ""
                ? undefined
                : value === "all"
                  ? ALL_STATUSES
                  : [value],
          })
        }}
        aria-label={t("resources.filter.status")}
        className={cn(selectClass, status && selectActive)}
      >
        <option value="">{t("resources.filter.status")}</option>
        <option value="missing">{t("status.missing")}</option>
        <option value="deleted">{t("status.deleted")}</option>
        <option value="all">{t("resources.filter.allIncludingDeleted")}</option>
      </select>

      <Popover>
        <PopoverTrigger asChild>
          <Button variant="outline" className="px-2.5">
            <ListFilter />
            {t("resources.filter.more")}
            {moreCount > 0 && (
              <span className="rounded-sm bg-primary-soft px-1.5 text-xs font-semibold text-primary">
                {moreCount}
              </span>
            )}
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="max-h-[70vh] w-[min(360px,calc(100vw-32px))] overflow-auto p-3"
        >
          <div className="flex flex-col gap-4">
            {scope?.kind !== "acct" && (
              <Section label={t("resources.group.accountId")}>
                <OptionList
                  options={facets.accountId ?? []}
                  selected={asList(state.filters.accountId)}
                  onChange={(next) =>
                    setFilters({ accountId: next.length ? next : undefined })
                  }
                />
              </Section>
            )}
            {scope?.kind !== "app" && (
              <Section label={t("resources.group.eimId")}>
                <OptionList
                  options={facets.eimId ?? []}
                  selected={asList(state.filters.eimId)}
                  onChange={(next) =>
                    setFilters({ eimId: next.length ? next : undefined })
                  }
                />
              </Section>
            )}
            <Section label={t("resources.group.baId")}>
              <OptionList
                options={facets.baId ?? []}
                selected={asList(state.filters.baId)}
                onChange={(next) =>
                  setFilters({ baId: next.length ? next : undefined })
                }
              />
            </Section>
            <Section label={t("resources.group.tags")}>
              <TagFilter
                value={tagFilters(state.filters)}
                onChange={(next) =>
                  setFilters({
                    tag: next.tag.length ? next.tag : undefined,
                    tagMissing: next.tagMissing.length
                      ? next.tagMissing
                      : undefined,
                  })
                }
              />
            </Section>
            {typeColumns.map((column) => (
              <Section key={column.key} label={column.label}>
                <FilterControl
                  column={column}
                  value={state.filters[column.key]}
                  options={facets[column.key]}
                  onChange={(value) => setFilters({ [column.key]: value })}
                />
              </Section>
            ))}
          </div>
        </PopoverContent>
      </Popover>

      {hasFilters && (
        <Button variant="link" onClick={clearAll}>
          {t("action.clearFilters")}
        </Button>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" className="ml-auto">
            <Download />
            {t("action.export")}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-64">
          <p className="px-3 py-1.5 text-xs text-muted-foreground">
            {t("resources.filter.exportTip", { count: total })}
          </p>
          <DropdownMenuItem asChild>
            <a href={exportHref("csv")}>{t("action.exportCsv")}</a>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <a href={exportHref("jsonl")}>{t("action.exportJsonl")}</a>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </search>
  )
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-xs font-semibold text-muted-foreground">{label}</h3>
      {children}
    </section>
  )
}

function asList(value: FilterValue | undefined): string[] {
  if (Array.isArray(value)) return value
  return typeof value === "string" ? [value] : []
}

function tagFilters(filters: TableState["filters"]): TagFilters {
  return { tag: asList(filters.tag), tagMissing: asList(filters.tagMissing) }
}

function statusValue(value: FilterValue | undefined): string {
  const list = asList(value)
  if (list.includes("deleted")) {
    return list.length === ALL_STATUSES.length ? "all" : "deleted"
  }
  return list.includes("missing") ? "missing" : ""
}
