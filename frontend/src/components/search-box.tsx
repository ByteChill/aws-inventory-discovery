import { useNavigate } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { type FormEvent, useState } from "react"

import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

const ARN = /^arn:aws[\w-]*:/i

/** An ARN opens the resource; anything else searches the resource list. */
export function SearchBox({
  size = "default",
  className,
}: {
  size?: "default" | "large"
  className?: string
}) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [value, setValue] = useState("")

  function submit(e: FormEvent) {
    e.preventDefault()
    const q = value.trim()
    if (!q) return
    if (ARN.test(q)) {
      navigate({ to: "/resources/$id", params: { id: q } })
    } else {
      navigate({ to: "/resources", search: { q } })
    }
  }

  return (
    <search className={cn("relative block", className)}>
      <form onSubmit={submit}>
        <Search
          aria-hidden="true"
          className={cn(
            "pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted-foreground",
            size === "large" ? "left-3.5 size-5" : "left-2.5 size-4",
          )}
        />
        <input
          type="search"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={t("search.placeholder")}
          aria-label={t("search.label")}
          className={cn(
            "w-full rounded-md border border-input bg-card placeholder:text-muted-foreground",
            size === "large"
              ? "h-12 pr-4 pl-11 text-base"
              : "h-8 pr-3 pl-8 text-sm",
          )}
        />
      </form>
    </search>
  )
}
