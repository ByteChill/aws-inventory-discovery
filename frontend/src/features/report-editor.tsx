import { useState } from "react"

import { useReportFields, useSaveReport } from "@/api/reports"
import type {
  ConditionInput,
  ReportDetail,
  ReportFieldInfo,
  ReportInput,
} from "@/client"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { cn } from "@/lib/utils"
import { ConditionEditor } from "./report-conditions"

const EMPTY_WHERE: ConditionInput = {
  all: [{ field: "resourceId", op: "exists" }],
}

/** Create or edit a report. The rule never runs here: the results page evaluates it. */
export function ReportEditor({
  initial,
  types,
  onClose,
  onSaved,
}: {
  initial?: ReportDetail
  types: { typeName: string; label: string }[]
  onClose: () => void
  onSaved: (saved: ReportDetail) => void
}) {
  const { t } = useI18n()
  const [name, setName] = useState(initial?.name ?? "")
  const [description, setDescription] = useState(initial?.description ?? "")
  const [subjectType, setSubjectType] = useState(initial?.subjectType ?? "")
  const [where, setWhere] = useState<ConditionInput>(
    (initial?.where as ConditionInput | undefined) ?? EMPTY_WHERE,
  )
  const [columns, setColumns] = useState<string[]>(initial?.columns ?? [])
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const fields = useReportFields(subjectType || undefined)
  const save = useSaveReport(initial?.id)

  function changeSubject(value: string) {
    setSubjectType(value)
    // A field of the old type may not exist in the new one.
    setWhere(EMPTY_WHERE)
    setColumns([])
  }

  function toggleColumn(key: string) {
    setColumns((current) =>
      current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key],
    )
  }

  async function submit() {
    const body: ReportInput = {
      name: name.trim(),
      description: description.trim() || null,
      subjectType: subjectType || null,
      where,
      columns,
      enabled,
    }
    try {
      onSaved(await save.mutateAsync(body))
    } catch {
      // `save.error` holds the API's message; the dialog stays open for a fix.
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={t(initial ? "reports.editTitle" : "reports.createTitle")}
      className="w-[min(56rem,calc(100vw-2rem))]"
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <div className="grid grid-cols-2 gap-3 max-[640px]:grid-cols-1">
          <label className="flex flex-col gap-1 text-sm">
            {t("reports.name")}
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              required
              className="h-8 rounded-md border border-input bg-card px-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("reports.subjectType")}
            <select
              value={subjectType}
              onChange={(e) => changeSubject(e.target.value)}
              className="h-8 rounded-md border border-input bg-card px-2"
            >
              <option value="">{t("reports.allTypes")}</option>
              {types.map((type) => (
                <option key={type.typeName} value={type.typeName}>
                  {type.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          {t("reports.description")}
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={2000}
            className="h-8 rounded-md border border-input bg-card px-2"
          />
        </label>

        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t("reports.conditions")}</h3>
          {fields.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : fields.data ? (
            <>
              <ConditionEditor
                value={where}
                fields={fields.data}
                onChange={setWhere}
              />
              {fields.data.some((field) => field.group === "relation") && (
                <p className="text-xs text-muted-foreground">
                  {t("reports.relationHint")}
                </p>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              {errorText(t, fields.error)}
            </p>
          )}
        </section>

        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t("reports.columns")}</h3>
          <p className="text-xs text-muted-foreground">
            {t("reports.columnsHint")}
          </p>
          {fields.data && (
            <ColumnPicker
              fields={fields.data}
              selected={columns}
              onToggle={toggleColumn}
            />
          )}
        </section>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          {t("reports.enabled")}
        </label>

        {save.error && (
          <p role="alert" className="text-sm text-danger">
            {errorText(t, save.error)}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            {t("action.cancel")}
          </Button>
          <Button type="submit" disabled={!name.trim() || save.isPending}>
            {save.isPending ? t("reports.saving") : t("reports.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function ColumnPicker({
  fields,
  selected,
  onToggle,
}: {
  fields: ReportFieldInfo[]
  selected: string[]
  onToggle: (key: string) => void
}) {
  return (
    <div className="grid max-h-40 grid-cols-2 gap-x-4 gap-y-1 overflow-auto rounded-md border px-3 py-2 max-[640px]:grid-cols-1">
      {fields.map((field) => (
        <label
          key={field.key}
          className={cn(
            "flex items-center gap-2 text-sm",
            field.group !== "common" && "text-foreground",
          )}
        >
          <input
            type="checkbox"
            checked={selected.includes(field.key)}
            onChange={() => onToggle(field.key)}
          />
          <span className="truncate">{field.label}</span>
          {field.array && (
            <span className="text-xs text-muted-foreground">
              {field.type === "tags" ? "" : "[]"}
            </span>
          )}
        </label>
      ))}
    </div>
  )
}
