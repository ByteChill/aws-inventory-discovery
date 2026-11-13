import { Plus, X } from "lucide-react"
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
      <Button
        type="submit"
        variant="outline"
        className="self-end"
        disabled={!key.trim()}
      >
        <Plus />
        {t("action.add")}
      </Button>
      {(value.tag.length > 0 || value.tagMissing.length > 0) && (
        <ul className="flex flex-wrap gap-1.5 pt-0.5">
          {value.tag.map((entry) => (
            <li key={`tag:${entry}`}>
              <TagChip
                label={entry}
                onRemove={() =>
                  onChange({
                    ...value,
                    tag: value.tag.filter((v) => v !== entry),
                  })
                }
              />
            </li>
          ))}
          {value.tagMissing.map((entry) => (
            <li key={`missing:${entry}`}>
              <TagChip
                label={t("resources.tags.missingChip", { tag: entry })}
                onRemove={() =>
                  onChange({
                    ...value,
                    tagMissing: value.tagMissing.filter((v) => v !== entry),
                  })
                }
              />
            </li>
          ))}
        </ul>
      )}
    </form>
  )
}

function TagChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  const { t } = useI18n()
  return (
    <span className="inline-flex items-center gap-1 rounded-full border bg-subtle py-0.5 pr-1 pl-2.5 text-xs">
      <span className="max-w-56 truncate" title={label}>
        {label}
      </span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("resources.tags.remove", { tag: label })}
        className="rounded-full p-0.5 text-muted-foreground hover:bg-subtle-strong hover:text-foreground"
      >
        <X className="size-3" />
      </button>
    </span>
  )
}
