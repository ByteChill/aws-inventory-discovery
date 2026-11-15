import { useEffect, useState } from "react"

import {
  useBatchPolicy,
  usePreviewScanPolicy,
  useRegionOptions,
} from "@/api/accounts"
import type { AccountBatchUpdate, AccountSelector } from "@/client"
import { InlineError } from "@/components/inline-state"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"

type PolicyAction = "keep" | "enable" | "disable"
type RegionAction = "keep" | "all" | "selected"

export function BulkPolicyDialog({
  open,
  selectedIds,
  filterSelector,
  filterCount,
  onClose,
  onUpdated,
}: {
  open: boolean
  selectedIds: string[]
  filterSelector: AccountSelector
  filterCount: number
  onClose: () => void
  onUpdated: (updated: number) => void
}) {
  const { t } = useI18n()
  const regions = useRegionOptions()
  const preview = usePreviewScanPolicy()
  const batch = useBatchPolicy()
  const [scope, setScope] = useState<"selected" | "filter">("selected")
  const [action, setAction] = useState<PolicyAction>("keep")
  const [regionAction, setRegionAction] = useState<RegionAction>("keep")
  const [selectedRegions, setSelectedRegions] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!open) return
    setScope(selectedIds.length ? "selected" : "filter")
    setAction("keep")
    setRegionAction("keep")
    setSelectedRegions(new Set())
    preview.reset()
    batch.reset()
  }, [open, selectedIds.length, preview.reset, batch.reset])

  const selector: AccountSelector =
    scope === "selected" ? { accountIds: selectedIds } : filterSelector
  const canSave = action !== "keep" || regionAction !== "keep"
  const regionList = [
    ...new Set([
      ...(regions.data?.reachableRegions ?? []),
      ...(regions.data?.knownRegions ?? []),
    ]),
  ].sort()

  function policyFields(): Omit<AccountBatchUpdate, "selector"> {
    const body: Omit<AccountBatchUpdate, "selector"> = {}
    if (action !== "keep") body.scanEnabled = action === "enable"
    if (regionAction === "all") body.scanRegionMode = "all"
    if (regionAction === "selected") {
      body.scanRegionMode = "selected"
      body.selectedRegions = [...selectedRegions].sort()
    }
    return body
  }

  function runPreview() {
    preview.mutate({ selector, ...policyFields() })
  }

  function save() {
    batch.mutate(
      { selector, ...policyFields() },
      {
        onSuccess: (result) => {
          onUpdated(result.updated)
          onClose()
        },
      },
    )
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("accounts.bulkPolicy")}
      className="w-[min(42rem,calc(100vw-2rem))]"
    >
      <div className="flex flex-col gap-4">
        <fieldset>
          <legend className="mb-2 text-xs font-semibold text-muted-foreground">
            {t("accounts.scope")}
          </legend>
          <div className="flex flex-col gap-2">
            {selectedIds.length > 0 && (
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="bulk-scope"
                  checked={scope === "selected"}
                  onChange={() => setScope("selected")}
                  className="accent-[var(--accent)]"
                />
                {t("accounts.scope.selected", { count: selectedIds.length })}
              </label>
            )}
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="bulk-scope"
                checked={scope === "filter"}
                onChange={() => setScope("filter")}
                className="accent-[var(--accent)]"
              />
              {t("accounts.scope.filter", { count: filterCount })}
            </label>
          </div>
        </fieldset>

        <fieldset>
          <legend className="mb-2 text-xs font-semibold text-muted-foreground">
            {t("accounts.scanEnabled")}
          </legend>
          <div className="flex flex-wrap gap-4">
            {(["keep", "enable", "disable"] as const).map((value) => (
              <label key={value} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="bulk-action"
                  checked={action === value}
                  onChange={() => setAction(value)}
                  className="accent-[var(--accent)]"
                />
                {t(`accounts.policyAction.${value}` as MessageKey)}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend className="mb-2 text-xs font-semibold text-muted-foreground">
            {t("accounts.regionMode")}
          </legend>
          <div className="flex flex-wrap gap-4">
            {(["keep", "all", "selected"] as const).map((value) => (
              <label key={value} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="bulk-region-action"
                  checked={regionAction === value}
                  onChange={() => setRegionAction(value)}
                  className="accent-[var(--accent)]"
                />
                {t(`accounts.regionAction.${value}` as MessageKey)}
              </label>
            ))}
          </div>
        </fieldset>

        {regionAction === "selected" && (
          <fieldset>
            <legend className="mb-2 text-xs font-semibold text-muted-foreground">
              {t("accounts.selectedRegions")}
            </legend>
            <div className="grid max-h-52 gap-1 overflow-auto rounded-md border p-2 sm:grid-cols-2">
              {regionList.map((region) => (
                <label key={region} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selectedRegions.has(region)}
                    onChange={(e) => {
                      const next = new Set(selectedRegions)
                      if (e.target.checked) next.add(region)
                      else next.delete(region)
                      setSelectedRegions(next)
                    }}
                    aria-label={region}
                    className="accent-[var(--accent)]"
                  />
                  <span className="font-mono text-xs">{region}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <div className="rounded-md border bg-subtle px-3 py-2">
          <p className="mb-2 text-xs font-semibold text-muted-foreground">
            {t("accounts.preview")}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={runPreview}>
              {t("accounts.preview")}
            </Button>
            {preview.data && (
              <p role="status" className="text-sm">
                {t("accounts.previewResult", {
                  accounts: preview.data.accounts,
                  regions: preview.data.regions,
                  tasks: preview.data.estimatedTasks,
                })}
                {preview.data.skippedDisabledAccounts > 0 &&
                  ` ${t("accounts.previewDisabled", {
                    count: preview.data.skippedDisabledAccounts,
                  })}`}
                {preview.data.accountsWithoutRegionData > 0 &&
                  ` ${t("accounts.previewNoRegionData", {
                    count: preview.data.accountsWithoutRegionData,
                  })}`}
              </p>
            )}
          </div>
          {preview.error && (
            <InlineError message={errorText(t, preview.error)} />
          )}
        </div>

        {batch.error && <InlineError message={errorText(t, batch.error)} />}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            {t("action.cancel")}
          </Button>
          <Button onClick={save} disabled={!canSave || batch.isPending}>
            {t("action.save")}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
