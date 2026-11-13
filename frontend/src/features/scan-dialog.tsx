import { useNavigate } from "@tanstack/react-router"
import { X } from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import { useFacets, useResourceTypes } from "@/api/hooks"
import {
  newIdempotencyKey,
  useAllAccounts,
  useCreateScan,
  useScanSummary,
} from "@/api/scans"
import { InlineError, QueryRegion } from "@/components/inline-state"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatCount } from "@/lib/format"
import { cn } from "@/lib/utils"

const MAX_ACCOUNTS = 50

type Preset = { accountIds?: string[]; eimId?: string }
type Item = { value: string; label: string }

function Choices({
  label,
  allLabel,
  items,
  selected,
  onChange,
}: {
  label: string
  allLabel: string
  items: Item[]
  selected: Set<string>
  onChange: (next: Set<string>) => void
}) {
  return (
    <fieldset>
      <legend className="mb-2 font-semibold">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {[{ value: "", label: allLabel }, ...items].map((item) => {
          const active = item.value
            ? selected.has(item.value)
            : selected.size === 0
          return (
            <button
              key={item.value}
              type="button"
              aria-pressed={active}
              onClick={() => {
                const next = new Set(selected)
                if (!item.value) next.clear()
                else if (next.has(item.value)) next.delete(item.value)
                else next.add(item.value)
                onChange(next)
              }}
              className={cn(
                "rounded-full border px-3 py-1 text-xs",
                active
                  ? "border-primary bg-primary-soft text-primary"
                  : "hover:bg-subtle",
              )}
            >
              {item.label}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

/** A selected-account review stays visible while the account search changes. */
export function ScanDialog({
  open,
  onClose,
  preset,
}: {
  open: boolean
  onClose: () => void
  preset?: Preset
}) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const accounts = useAllAccounts(open)
  const facets = useFacets(["awsRegion"], {}, open)
  const types = useResourceTypes()
  const summary = useScanSummary(open)
  const create = useCreateScan()
  const [all, setAll] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [regions, setRegions] = useState<Set<string>>(new Set())
  const [resourceTypes, setResourceTypes] = useState<Set<string>>(new Set())
  const [q, setQ] = useState("")
  const [key, setKey] = useState(newIdempotencyKey)
  const [presetApplied, setPresetApplied] = useState(false)
  // Controlled: picking the first account must not close the picker.
  const [pickerOpen, setPickerOpen] = useState(true)

  // Reset once on opening, then apply an application preset after accounts arrive.
  // biome-ignore lint/correctness/useExhaustiveDependencies: opening starts a fresh form
  useEffect(() => {
    if (!open) return
    setAll(false)
    setPicked(new Set(preset?.accountIds))
    setRegions(new Set())
    setResourceTypes(new Set())
    setQ("")
    setKey(newIdempotencyKey())
    setPresetApplied(!preset?.eimId)
    setPickerOpen(!preset?.accountIds?.length)
    create.reset()
  }, [open])
  useEffect(() => {
    if (!open || presetApplied || !accounts.data || !preset?.eimId) return
    setPicked(
      new Set(
        accounts.data
          .filter((a) => a.eimId === preset.eimId)
          .map((a) => a.accountId),
      ),
    )
    setPresetApplied(true)
  }, [open, presetApplied, accounts.data, preset?.eimId])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (accounts.data ?? []).filter(
      (a) =>
        !needle ||
        [a.accountName, a.accountId, a.eimId, a.eimApp, a.baId]
          .join(" ")
          .toLowerCase()
          .includes(needle),
    )
  }, [accounts.data, q])
  const selected = (accounts.data ?? []).filter((a) => picked.has(a.accountId))
  const regionItems = (facets.data?.awsRegion ?? [])
    .filter((b) => b.value !== "global")
    .map((b) => ({ value: b.value, label: b.label }))
  const typeItems = (types.data ?? []).map((item) => ({
    value: item.typeName,
    label: item.label,
  }))
  const sweep = summary.data?.sweep
  const tooMany = !all && picked.size > MAX_ACCOUNTS
  // An application preset needs the account list to turn into accounts, but an
  // all-accounts scan does not, so a failed account fetch cannot block it.
  const canSubmit =
    (all || picked.size > 0) &&
    !tooMany &&
    !create.isPending &&
    (presetApplied || !preset?.eimId)

  function submit() {
    create.mutate(
      {
        key,
        spec: all
          ? { all: true }
          : {
              accountIds: [...picked],
              ...(regions.size ? { awsRegions: [...regions] } : {}),
              ...(resourceTypes.size
                ? { resourceTypes: [...resourceTypes] }
                : {}),
            },
      },
      {
        onSuccess: (scan) => {
          onClose()
          navigate({ to: "/scans/$id", params: { id: String(scan.id) } })
        },
      },
    )
  }

  return (
    <Dialog open={open} onClose={onClose} title={t("action.scan")}>
      <div className="flex min-w-0 flex-col gap-4">
        <fieldset className="flex gap-1 rounded-lg bg-subtle p-1">
          <legend className="sr-only">{t("scans.form.accounts")}</legend>
          {[false, true].map((value) => (
            <button
              key={String(value)}
              type="button"
              aria-pressed={all === value}
              onClick={() => setAll(value)}
              className={cn(
                "flex-1 rounded-md px-3 py-2 font-semibold",
                all === value
                  ? "bg-card text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(
                value
                  ? "scans.form.allAccounts"
                  : "scans.form.specificAccounts",
              )}
            </button>
          ))}
        </fieldset>
        {all ? (
          <>
            <p className="text-muted-foreground">
              {t("scans.form.organizationNote", {
                count: accounts.data?.length ?? 0,
              })}
            </p>
            {sweep && (
              <div className="rounded-lg border bg-subtle p-3">
                {t("scans.form.existingSweep", { id: sweep.scanRequestId })}
              </div>
            )}
          </>
        ) : (
          <>
            <QueryRegion
              isLoading={accounts.isLoading}
              error={accounts.error}
              onRetry={() => accounts.refetch()}
              isEmpty={false}
              emptyMessage={t("scans.form.noAccounts")}
            >
              <div className="flex min-w-0 flex-col gap-1.5">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3>{t("scans.form.selected", { count: picked.size })}</h3>
                  {picked.size > 0 && (
                    <button
                      type="button"
                      onClick={() => setPicked(new Set())}
                      className="text-primary hover:underline"
                    >
                      {t("scans.form.clearAccounts")}
                    </button>
                  )}
                </div>
                {selected.length ? (
                  <ul className="max-h-48 overflow-auto rounded-lg border">
                    {selected.map((a) => (
                      <li
                        key={a.accountId}
                        className="flex items-center gap-2 border-b px-3 py-2 last:border-b-0"
                      >
                        <div className="min-w-0 flex-1">
                          <span className="block truncate">
                            {a.accountName}
                          </span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {a.accountId}
                          </span>
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {t(`env.${a.accountType}`)}
                        </span>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t("scans.form.removeAccount", {
                            name: a.accountName,
                          })}
                          onClick={() =>
                            setPicked((current) => {
                              const next = new Set(current)
                              next.delete(a.accountId)
                              return next
                            })
                          }
                        >
                          <X />
                        </Button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted-foreground">
                    {t("scans.form.pickAccounts")}
                  </p>
                )}
                <details
                  className="mt-2"
                  open={pickerOpen}
                  onToggle={(event) => setPickerOpen(event.currentTarget.open)}
                >
                  <summary className="cursor-pointer py-1 text-primary">
                    {t("scans.form.addAccounts")}
                  </summary>
                  <input
                    type="search"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    placeholder={t("scans.form.accountsSearch")}
                    aria-label={t("scans.form.accountsSearch")}
                    className="my-2 h-9 w-full rounded-md border border-input bg-card px-3"
                  />
                  <ul className="max-h-44 overflow-auto rounded-lg border p-1">
                    {shown.length === 0 && (
                      <li className="px-2 py-2 text-muted-foreground">
                        {t("scans.form.noAccounts")}
                      </li>
                    )}
                    {shown.map((a) => (
                      <li key={a.accountId}>
                        <label className="flex items-center gap-2 rounded-md px-2 py-2 hover:bg-subtle">
                          <input
                            type="checkbox"
                            checked={picked.has(a.accountId)}
                            onChange={(e) =>
                              setPicked((current) => {
                                const next = new Set(current)
                                if (e.target.checked) next.add(a.accountId)
                                else next.delete(a.accountId)
                                return next
                              })
                            }
                            className="accent-[var(--accent)]"
                          />
                          <span className="min-w-0 flex-1 truncate">
                            {a.accountName}
                          </span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {a.accountId}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                </details>
              </div>
            </QueryRegion>
            <Choices
              label={t("scans.form.regions")}
              allLabel={t("scans.form.allRegions")}
              items={regionItems}
              selected={regions}
              onChange={setRegions}
            />
            <Choices
              label={t("scans.form.types")}
              allLabel={t("scans.form.allTypes")}
              items={typeItems}
              selected={resourceTypes}
              onChange={setResourceTypes}
            />
          </>
        )}
        {create.error && <InlineError message={errorText(t, create.error)} />}
        <div className="sticky bottom-0 -mx-5 -mb-5 flex flex-col gap-3 border-t bg-card px-5 pt-3 pb-5">
          <p>
            {all
              ? t("scans.form.allScope")
              : t("scans.form.scope", {
                  accounts: formatCount(t, "accounts", picked.size),
                  regions: regions.size
                    ? formatCount(t, "regions", regions.size)
                    : t("scans.form.allRegions"),
                  types: resourceTypes.size
                    ? formatCount(t, "types", resourceTypes.size)
                    : t("scans.form.allTypes"),
                })}
          </p>
          {tooMany && <p className="text-warn">{t("scans.form.limit")}</p>}
          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={onClose}>
              {t("action.cancel")}
            </Button>
            <Button disabled={!canSubmit} onClick={submit}>
              {t("action.scan")}
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  )
}
