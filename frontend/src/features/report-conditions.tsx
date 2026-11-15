import { Plus, X } from "lucide-react"

import type { ConditionInput, ReportFieldInfo } from "@/client"
import { Button } from "@/components/ui/button"
import { type MessageKey, useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

type Op = NonNullable<ConditionInput["op"]>

/** The operators each field type accepts, in the order the editor lists them. */
const OPS: Record<string, Op[]> = {
  text: [
    "contains",
    "eq",
    "neq",
    "startsWith",
    "in",
    "notIn",
    "exists",
    "notExists",
  ],
  enum: ["eq", "neq", "in", "notIn", "exists", "notExists"],
  number: ["gte", "lte", "gt", "lt", "eq", "neq", "in", "notIn", "exists"],
  bool: ["eq", "neq"],
  datetime: [
    "olderThanDays",
    "beforeNow",
    "afterNow",
    "gte",
    "lte",
    "gt",
    "lt",
  ],
  tags: ["has", "hasValue", "missing"],
}

const NO_VALUE: Op[] = [
  "exists",
  "notExists",
  "missing",
  "has",
  "beforeNow",
  "afterNow",
]

export function defaultCondition(fields: ReportFieldInfo[]): ConditionInput {
  const field = fields[0]
  if (!field) return { field: "resourceId", op: "exists" }
  return leaf(field)
}

function leaf(field: ReportFieldInfo): ConditionInput {
  const op = OPS[field.type]?.[0] ?? "eq"
  const node: ConditionInput = { field: field.key, op }
  if (field.type === "tags") node.key = ""
  if (field.type === "bool") node.value = true
  return node
}

function valueNeeded(
  field: ReportFieldInfo | undefined,
  op: Op | null | undefined,
) {
  if (!field || !op || NO_VALUE.includes(op)) return false
  return field.type !== "tags" || op === "hasValue"
}

function parseValue(type: string, op: Op, raw: string): unknown {
  if (op === "olderThanDays") return raw === "" ? undefined : Number(raw)
  if (op === "in" || op === "notIn") {
    const parts = raw
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
    return type === "number" ? parts.map(Number) : parts
  }
  if (type === "number") return raw === "" ? undefined : Number(raw)
  if (type === "bool") return raw === "true"
  return raw
}

function rawValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ")
  if (value === null || value === undefined) return ""
  return String(value)
}

/** Root group of a saved tree: a lone leaf or `not` is read as an "all" of one. */
function asGroup(node: ConditionInput): {
  op: "all" | "any"
  children: ConditionInput[]
} {
  if (node.all) return { op: "all", children: node.all }
  if (node.any) return { op: "any", children: node.any }
  return { op: "all", children: [node] }
}

export function ConditionEditor({
  value,
  fields,
  onChange,
}: {
  value: ConditionInput
  fields: ReportFieldInfo[]
  onChange: (next: ConditionInput) => void
}) {
  const group = asGroup(value)
  return (
    <div className="flex flex-col gap-2">
      <GroupBody
        op={group.op}
        nodes={group.children}
        fields={fields}
        onChange={(children) => onChange({ [group.op]: children })}
        onOp={(op) => onChange({ [op]: group.children })}
      />
    </div>
  )
}

function GroupBody({
  op,
  nodes,
  fields,
  onChange,
  onOp,
}: {
  op: "all" | "any"
  nodes: ConditionInput[]
  fields: ReportFieldInfo[]
  onChange: (next: ConditionInput[]) => void
  onOp: (op: "all" | "any") => void
}) {
  const { t } = useI18n()
  const replace = (index: number, node: ConditionInput) =>
    onChange(nodes.map((item, i) => (i === index ? node : item)))
  const remove = (index: number) =>
    onChange(nodes.filter((_, i) => i !== index))

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <select
          value={op}
          aria-label={t("reports.condition.group")}
          onChange={(e) => onOp(e.target.value as "all" | "any")}
          className="h-7 rounded-md border border-input bg-card px-2 text-sm"
        >
          <option value="all">{t("reports.condition.all")}</option>
          <option value="any">{t("reports.condition.any")}</option>
        </select>
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            onChange([...nodes, { all: [defaultCondition(fields)] }])
          }
        >
          <Plus />
          {t("reports.condition.addGroup")}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onChange([...nodes, defaultCondition(fields)])}
        >
          <Plus />
          {t("reports.condition.addCondition")}
        </Button>
      </div>
      <ul className="flex flex-col gap-2">
        {nodes.map((node, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: a condition has no identity of its own
          <li key={index}>
            <NodeRow
              node={node}
              fields={fields}
              onChange={(next) => replace(index, next)}
              onRemove={nodes.length > 1 ? () => remove(index) : undefined}
            />
          </li>
        ))}
      </ul>
    </div>
  )
}

function NodeRow({
  node,
  fields,
  onChange,
  onRemove,
}: {
  node: ConditionInput
  fields: ReportFieldInfo[]
  onChange: (next: ConditionInput) => void
  onRemove?: () => void
}) {
  const { t } = useI18n()
  const negated = node.not != null
  const inner = negated ? (node.not as ConditionInput) : node

  return (
    <div className="flex items-start gap-2">
      <div
        className={cn(
          "min-w-0 flex-1",
          negated && "rounded-md border border-dashed px-2 py-1.5",
        )}
      >
        {negated && (
          <span className="mb-1 block text-xs text-muted-foreground">
            {t("reports.condition.negate")}
          </span>
        )}
        {inner.all || inner.any ? (
          <NestedGroup
            node={inner}
            fields={fields}
            onChange={(next) => onChange(negated ? { not: next } : next)}
          />
        ) : (
          <LeafEditor
            node={inner}
            fields={fields}
            onChange={(next) => onChange(negated ? { not: next } : next)}
          />
        )}
      </div>
      <div className="flex shrink-0 items-center">
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={negated}
          aria-label={t("reports.condition.negate")}
          title={t("reports.condition.negate")}
          className={cn("size-7 px-0", negated && "text-primary")}
          onClick={() => onChange(negated ? inner : { not: inner })}
        >
          NOT
        </Button>
        {onRemove && (
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label={t("reports.condition.remove")}
            onClick={onRemove}
          >
            <X />
          </Button>
        )}
      </div>
    </div>
  )
}

function NestedGroup({
  node,
  fields,
  onChange,
}: {
  node: ConditionInput
  fields: ReportFieldInfo[]
  onChange: (next: ConditionInput) => void
}) {
  const op = node.all ? "all" : "any"
  return (
    <GroupBody
      op={op}
      nodes={node[op] ?? []}
      fields={fields}
      onChange={(children) => onChange({ [op]: children })}
      onOp={(next) => onChange({ [next]: node[op] ?? [] })}
    />
  )
}

function LeafEditor({
  node,
  fields,
  onChange,
}: {
  node: ConditionInput
  fields: ReportFieldInfo[]
  onChange: (next: ConditionInput) => void
}) {
  const { t } = useI18n()
  const field = fields.find((item) => item.key === node.field) ?? fields[0]
  const ops = field ? (OPS[field.type] ?? []) : []
  const op = node.op ?? ops[0]
  const inputClass =
    "h-7 min-w-0 rounded-md border border-input bg-card px-2 text-sm"

  function changeField(key: string) {
    const next = fields.find((item) => item.key === key)
    if (next) onChange(leaf(next))
  }
  function changeOp(nextOp: Op) {
    const list = nextOp === "in" || nextOp === "notIn"
    const wasList = op === "in" || op === "notIn"
    let value =
      NO_VALUE.includes(nextOp) || list !== wasList ? undefined : node.value
    if (field?.type === "bool" && !NO_VALUE.includes(nextOp)) value ??= true
    onChange({ ...node, op: nextOp, value })
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <select
        value={field?.key ?? ""}
        aria-label={t("reports.condition.field")}
        onChange={(e) => changeField(e.target.value)}
        className={cn(inputClass, "max-w-56")}
      >
        {(["common", "type", "relation"] as const).map((group) => {
          const items = fields.filter((item) => item.group === group)
          if (!items.length) return null
          return (
            <optgroup
              key={group}
              label={t(`reports.fieldGroup.${group}` as MessageKey)}
            >
              {items.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </optgroup>
          )
        })}
      </select>
      {field?.type === "tags" && (
        <input
          value={node.key ?? ""}
          aria-label={t("reports.condition.key")}
          placeholder={t("reports.condition.key")}
          onChange={(e) => onChange({ ...node, key: e.target.value })}
          className={cn(inputClass, "w-24")}
        />
      )}
      <select
        value={op}
        aria-label={t("reports.condition.op")}
        onChange={(e) => changeOp(e.target.value as Op)}
        className={cn(inputClass, "max-w-32")}
      >
        {ops.map((item) => (
          <option key={item} value={item}>
            {t(`reports.op.${item}` as MessageKey)}
          </option>
        ))}
      </select>
      {field &&
        valueNeeded(field, op) &&
        (field.type === "bool" ? (
          <select
            value={rawValue(node.value) || "true"}
            aria-label={t("reports.condition.value")}
            onChange={(e) =>
              onChange({ ...node, value: e.target.value === "true" })
            }
            className={inputClass}
          >
            <option value="true">{t("common.yes")}</option>
            <option value="false">{t("common.no")}</option>
          </select>
        ) : (
          <input
            value={rawValue(node.value)}
            aria-label={t("reports.condition.value")}
            placeholder={
              op === "in" || op === "notIn"
                ? t("reports.condition.listHint")
                : op === "olderThanDays"
                  ? t("reports.condition.daysHint")
                  : field.type === "datetime"
                    ? "2026-11-01T00:00:00Z"
                    : t("reports.condition.value")
            }
            inputMode={
              field.type === "number" || op === "olderThanDays"
                ? "numeric"
                : undefined
            }
            onChange={(e) =>
              onChange({
                ...node,
                value: parseValue(field.type, op, e.target.value),
              })
            }
            className={cn(inputClass, "w-48 flex-1")}
          />
        ))}
    </div>
  )
}
