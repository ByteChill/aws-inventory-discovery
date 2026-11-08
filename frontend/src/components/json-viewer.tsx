import { ChevronDown, ChevronRight } from "lucide-react"
import { useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

type Json = unknown
type Entry = [key: string, value: Json]

const isContainer = (v: Json): v is object =>
  typeof v === "object" && v !== null

function entries(v: object): Entry[] {
  return Array.isArray(v)
    ? v.map((x, i) => [String(i), x])
    : Object.entries(v as Record<string, Json>)
}

function scalarText(v: Json): string {
  return typeof v === "string" ? JSON.stringify(v) : String(v)
}

/** Paths of every node that matches the query, and of all their ancestors. */
function matchPaths(root: Json, query: string): Set<string> {
  const found = new Set<string>()
  const q = query.toLowerCase()
  if (!q) return found
  const walk = (value: Json, path: string, key: string): boolean => {
    let hit = key.toLowerCase().includes(q)
    if (isContainer(value)) {
      for (const [k, v] of entries(value)) {
        if (walk(v, `${path}/${k}`, k)) hit = true
      }
    } else if (scalarText(value).toLowerCase().includes(q)) {
      hit = true
    }
    if (hit) found.add(path)
    return hit
  }
  walk(root, "", "")
  return found
}

type View = {
  query: string
  matches: Set<string>
  /** "auto": two levels, or the matches while searching. */
  base: "auto" | "all" | "none"
  overrides: Record<string, boolean>
  toggle: (path: string, open: boolean) => void
}

function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.toLowerCase()
  const at = q ? text.toLowerCase().indexOf(q) : -1
  if (at < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, at)}
      <mark className="rounded-sm bg-primary-soft text-primary">
        {text.slice(at, at + q.length)}
      </mark>
      {text.slice(at + q.length)}
    </>
  )
}

function Node({
  name,
  value,
  path,
  depth,
  view,
}: {
  name: string | null
  value: Json
  path: string
  depth: number
  view: View
}) {
  const { t } = useI18n()
  const label =
    name === null ? null : (
      <span className="text-muted-foreground">
        <Highlight text={name} query={view.query} />
        {": "}
      </span>
    )

  if (!isContainer(value)) {
    return (
      <div className="py-px pl-5 break-all">
        {label}
        <span>
          <Highlight text={scalarText(value)} query={view.query} />
        </span>
      </div>
    )
  }

  const list = entries(value)
  const brackets = Array.isArray(value) ? ["[", "]"] : ["{", "}"]
  const auto = view.query ? view.matches.has(path) : depth < 2
  const open =
    view.overrides[path] ??
    (view.base === "all" ? true : view.base === "none" ? false : auto)

  return (
    <div>
      <button
        type="button"
        onClick={() => view.toggle(path, !open)}
        aria-expanded={open}
        className="flex w-full items-start gap-1 py-px text-left hover:bg-subtle"
      >
        {open ? (
          <ChevronDown className="mt-1 size-3.5 shrink-0" />
        ) : (
          <ChevronRight className="mt-1 size-3.5 shrink-0" />
        )}
        <span className="break-all">
          {label}
          {brackets[0]}
          {!open &&
            list.length > 0 &&
            ` ${t("unit.items", { count: list.length })} ${brackets[1]}`}
          {list.length === 0 && brackets[1]}
        </span>
      </button>
      {open && list.length > 0 && (
        <div className="ml-2 border-l pl-3">
          {list.map(([k, v]) => (
            <Node
              key={k}
              name={Array.isArray(value) ? null : k}
              value={v}
              path={`${path}/${k}`}
              depth={depth + 1}
              view={view}
            />
          ))}
          <div className="pl-5">{brackets[1]}</div>
        </div>
      )}
    </div>
  )
}

/** Collapsible JSON with search; matches are highlighted and their branches opened. */
export function JsonViewer({
  value,
  searchable = true,
  searchLabel,
  className,
}: {
  value: Json
  searchable?: boolean
  searchLabel?: string
  className?: string
}) {
  const { t } = useI18n()
  const [query, setQuery] = useState("")
  const [base, setBase] = useState<View["base"]>("auto")
  const [overrides, setOverrides] = useState<Record<string, boolean>>({})
  const matches = useMemo(() => matchPaths(value, query), [value, query])
  const noMatch = query !== "" && matches.size === 0

  function setAll(next: "all" | "none") {
    setBase(next)
    setOverrides({})
  }

  const view: View = {
    query,
    matches,
    base,
    overrides,
    toggle: (path, open) => setOverrides((o) => ({ ...o, [path]: open })),
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        {searchable && (
          <Input
            type="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setOverrides({})
            }}
            placeholder={searchLabel ?? t("detail.configSearch")}
            aria-label={searchLabel ?? t("detail.configSearch")}
            className="w-64"
          />
        )}
        <Button variant="outline" onClick={() => setAll("all")}>
          {t("action.expandAll")}
        </Button>
        <Button variant="outline" onClick={() => setAll("none")}>
          {t("action.collapseAll")}
        </Button>
      </div>
      <div className="overflow-auto rounded-lg border bg-card p-3 font-mono text-xs">
        {noMatch ? (
          <p className="font-sans text-sm text-muted-foreground">
            {t("detail.noMatch")}
          </p>
        ) : (
          <Node name={null} value={value} path="" depth={0} view={view} />
        )}
      </div>
    </div>
  )
}
