import { type FormEvent, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { type MessageKey, useI18n } from "@/i18n"

export type TagFilters = { tag: string[]; tagMissing: string[] }

type Mode = "has" | "missing" | "equals"
const MODES: Mode[] = ["has", "missing", "equals"]

/**
 * Tag filter: has a key, lacks a key, or key equals a value. "Has" and "equals"
 * go to the API's `tag` (`key` / `key:value`), "lacks" to `tagMissing`.
 */
export function TagFilter({
  value,
  onChange,
}: {
  value: TagFilters
  onChange: (next: TagFilters) => void
}) {
  const { t } = useI18n()
  const [mode, setMode] = useState<Mode>("has")
  const [key, setKey] = useState("")
  const [val, setVal] = useState("")

  function submit(e: FormEvent) {
    e.preventDefault()
    const k = key.trim()
    if (!k) return
    if (mode === "missing") {
      if (!value.tagMissing.includes(k))
        onChange({ ...value, tagMissing: [...value.tagMissing, k] })
    } else {
      const entry = mode === "equals" ? `${k}:${val.trim()}` : k
      if (!value.tag.includes(entry))
        onChange({ ...value, tag: [...value.tag, entry] })
    }
    setKey("")
    setVal("")
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-1.5">
      <div
        role="radiogroup"
        aria-label={t("resources.tags.mode")}
        className="flex gap-1"
      >
        {MODES.map((m) => (
          <Button
            key={m}
            type="button"
            role="radio"
            aria-checked={mode === m}
            variant={mode === m ? "default" : "outline"}
            size="sm"
            className="flex-1"
            onClick={() => setMode(m)}
          >
            {t(`resources.tags.${m}` as MessageKey)}
          </Button>
        ))}
      </div>
      <Input
        value={key}
        onChange={(e) => setKey(e.target.value)}
        placeholder={t("resources.tags.key")}
        aria-label={t("resources.tags.key")}
      />
      {mode === "equals" && (
        <Input
          value={val}
          onChange={(e) => setVal(e.target.value)}
          placeholder={t("resources.tags.value")}
          aria-label={t("resources.tags.value")}
        />
      )}
      <Button type="submit" variant="outline" disabled={!key.trim()}>
        {t("action.add")}
      </Button>
    </form>
  )
}
