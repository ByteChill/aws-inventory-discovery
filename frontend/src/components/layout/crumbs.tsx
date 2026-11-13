import { ChevronRight } from "lucide-react"
import type { ReactNode } from "react"

import { useI18n } from "@/i18n"

/** The prototype's breadcrumb: muted text, a chevron between items, no card. */
export function Breadcrumb({
  items,
  label,
}: {
  items: { key: string; content: ReactNode }[]
  label?: string
}) {
  const { t } = useI18n()
  return (
    <nav
      aria-label={label ?? t("breadcrumb.label")}
      className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground"
    >
      {items.map((item, index) => (
        <span key={item.key} className="flex items-center gap-1.5">
          {index > 0 && <ChevronRight aria-hidden="true" className="size-3" />}
          {index === items.length - 1 ? (
            <span aria-current="page">{item.content}</span>
          ) : (
            item.content
          )}
        </span>
      ))}
    </nav>
  )
}
