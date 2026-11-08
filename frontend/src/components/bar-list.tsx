import { Link } from "@tanstack/react-router"

import { useI18n } from "@/i18n"
import { formatNumber } from "@/lib/format"

export type BarItem = { value: string; label: string; count: number }

/** Horizontal bars; each row opens the resource list filtered to that value. */
export function BarList({
  items,
  filterKey,
}: {
  items: BarItem[]
  filterKey: "resourceType" | "accountType"
}) {
  const { locale } = useI18n()
  const max = Math.max(1, ...items.map((i) => i.count))
  return (
    <ul className="flex flex-col">
      {items.map((item) => (
        <li key={item.value}>
          <Link
            to="/resources"
            search={{ f: { [filterKey]: [item.value] } }}
            className="grid grid-cols-[minmax(6rem,12rem)_1fr_auto] items-start gap-x-3 rounded-sm px-1 py-1.5 hover:bg-subtle"
          >
            <span className="break-words">{item.label}</span>
            <span className="mt-2 h-2 rounded-sm bg-subtle-strong">
              <span
                className="block h-full rounded-sm bg-primary"
                style={{ width: `${(item.count / max) * 100}%` }}
              />
            </span>
            <span className="min-w-12 text-right">
              {formatNumber(item.count, locale)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  )
}
