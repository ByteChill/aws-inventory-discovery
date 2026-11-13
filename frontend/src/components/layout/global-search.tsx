import { useNavigate } from "@tanstack/react-router"
import { Search } from "lucide-react"
import { type FormEvent, type RefObject, useState } from "react"

import { useI18n } from "@/i18n"
import { resolveGlobalSearch, type SearchTarget } from "@/lib/global-search"
import { cn } from "@/lib/utils"

/**
 * The top bar's global search. It looks things up and navigates; it never
 * changes the list filters on the page you are leaving (spec 1.3).
 */
export function GlobalSearch({
  inputRef,
  className,
  onSubmitted,
}: {
  inputRef: RefObject<HTMLInputElement | null>
  className?: string
  onSubmitted?: () => void
}) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [value, setValue] = useState("")
  const [pending, setPending] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const q = value.trim()
    if (!q || pending) return
    setPending(true)
    try {
      navigateTarget(await resolveGlobalSearch(q))
      setValue("")
      onSubmitted?.()
    } catch {
      // A lookup that fails on the API still leaves a usable search.
      navigate({ to: "/resources", search: { q } })
      setValue("")
      onSubmitted?.()
    } finally {
      setPending(false)
    }
  }

  function navigateTarget(target: SearchTarget) {
    switch (target.kind) {
      case "resource":
        navigate({ to: "/resources/$id", params: { id: target.id } })
        return
      case "scope":
        navigate({ to: "/", search: { s: target.s } })
        return
      case "scan":
        navigate({ to: "/scans/$id", params: { id: String(target.id) } })
        return
      case "task":
        navigate({ to: "/tasks/$id", params: { id: String(target.id) } })
        return
      case "list":
        navigate({ to: "/resources", search: { q: target.q } })
    }
  }

  return (
    <search
      className={cn(
        "flex w-[min(460px,60%)] items-center gap-2 text-muted-foreground max-[800px]:w-full",
        className,
      )}
      aria-busy={pending}
    >
      <form onSubmit={submit} className="flex w-full items-center gap-2">
        <Search aria-hidden="true" className="-ml-0.5 size-4 shrink-0" />
        <input
          ref={inputRef}
          type="search"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={t("search.globalPlaceholder")}
          aria-label={t("search.globalLabel")}
          title={t("search.shortcut")}
          className="h-9 w-full min-w-0 border-0 bg-transparent px-0 text-foreground placeholder:text-muted-foreground focus-visible:outline-0"
        />
      </form>
    </search>
  )
}
