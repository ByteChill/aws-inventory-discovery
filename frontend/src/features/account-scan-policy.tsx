import { useEffect, useMemo, useState } from "react"

import { useUpdateAccount } from "@/api/accounts"
import type { AccountDetail, AccountRegionInfo } from "@/client"
import { InlineError } from "@/components/inline-state"
import { Button } from "@/components/ui/button"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { cn } from "@/lib/utils"

export function AccountScanPolicy({ account }: { account: AccountDetail }) {
  const { t } = useI18n()
  const update = useUpdateAccount()
  const initialSelected = useMemo(
    () =>
      new Set(
        account.regions
          .filter((region) => region.selected)
          .map((region) => region.region),
      ),
    [account.regions],
  )
  const [enabled, setEnabled] = useState(account.scanEnabled)
  const [mode, setMode] = useState(account.scanRegionMode)
  const [selected, setSelected] = useState<Set<string>>(initialSelected)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setEnabled(account.scanEnabled)
    setMode(account.scanRegionMode)
    setSelected(initialSelected)
  }, [account.scanEnabled, account.scanRegionMode, initialSelected])

  const regions = [...account.regions].sort((a, b) =>
    a.region.localeCompare(b.region),
  )

  function toggleRegion(region: string, checked: boolean) {
    const next = new Set(selected)
    if (checked) next.add(region)
    else next.delete(region)
    setSelected(next)
    setSaved(false)
  }

  function save() {
    update.mutate(
      {
        accountId: account.accountId,
        body: {
          scanEnabled: enabled,
          scanRegionMode: mode,
          selectedRegions: [...selected].sort(),
        },
      },
      { onSuccess: () => setSaved(true) },
    )
  }

  const effectiveCount = regions.filter((region) =>
    mode === "all"
      ? region.reachable &&
        ["opt-in-not-required", "opted-in"].includes(region.optInStatus)
      : selected.has(region.region) &&
        region.reachable &&
        ["opt-in-not-required", "opted-in"].includes(region.optInStatus),
  ).length

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => {
              setEnabled(e.target.checked)
              setSaved(false)
            }}
            aria-label={t("accounts.scanEnabled")}
            className="mt-0.5 accent-[var(--accent)]"
          />
          <span>
            <span className="font-semibold">{t("accounts.scanEnabled")}</span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              {t(
                enabled
                  ? "accounts.scanEnabledHint"
                  : "accounts.scanStoppedHint",
              )}
            </span>
          </span>
        </label>
        <p className="text-xs text-muted-foreground">
          {t("accounts.nextSweepNotice")}
        </p>
      </div>

      <fieldset>
        <legend className="mb-2 text-xs font-semibold text-muted-foreground">
          {t("accounts.regionMode")}
        </legend>
        <div className="flex flex-wrap gap-4">
          {(["all", "selected"] as const).map((value) => (
            <label key={value} className="flex items-center gap-2">
              <input
                type="radio"
                name="scan-region-mode"
                value={value}
                checked={mode === value}
                onChange={() => {
                  setMode(value)
                  setSaved(false)
                }}
                className="accent-[var(--accent)]"
              />
              {t(`accounts.regionMode.${value}` as MessageKey)}
            </label>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {t("accounts.globalRegionsHint")}
        </p>
      </fieldset>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {t("accounts.effectiveRegions", { count: effectiveCount })}
        </p>
        {mode === "selected" && (
          <div className="flex gap-2">
            <Button
              variant="link"
              size="sm"
              onClick={() => {
                setSelected(new Set(regions.map((r) => r.region)))
                setSaved(false)
              }}
            >
              {t("accounts.selectAllRegions")}
            </Button>
            <Button
              variant="link"
              size="sm"
              onClick={() => {
                setSelected(new Set())
                setSaved(false)
              }}
            >
              {t("accounts.clearRegions")}
            </Button>
          </div>
        )}
      </div>

      <div className="grid gap-1 sm:grid-cols-2">
        {regions.map((region) => (
          <RegionRow
            key={region.region}
            region={region}
            mode={mode}
            checked={
              mode === "all" ? region.selected : selected.has(region.region)
            }
            onChecked={(checked) => toggleRegion(region.region, checked)}
          />
        ))}
      </div>

      {update.error && <InlineError message={errorText(t, update.error)} />}
      {saved && (
        <p role="status" className="text-primary">
          {t("accounts.savedForNextSweep")}
        </p>
      )}
      <div>
        <Button onClick={save} disabled={update.isPending}>
          {t("action.save")}
        </Button>
      </div>
    </div>
  )
}

function RegionRow({
  region,
  mode,
  checked,
  onChecked,
}: {
  region: AccountRegionInfo
  mode: "all" | "selected"
  checked: boolean
  onChecked: (checked: boolean) => void
}) {
  const { t } = useI18n()
  const unavailable =
    region.state === "account_disabled" || region.state === "unreachable"
  return (
    <label className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-subtle">
      <input
        type="checkbox"
        checked={checked}
        disabled={mode === "all"}
        onChange={(e) => onChecked(e.target.checked)}
        aria-label={region.region}
        className="accent-[var(--accent)]"
      />
      <span className="min-w-0 flex-1 truncate font-mono text-xs">
        {region.region}
      </span>
      <span
        className={cn(
          "shrink-0 text-xs",
          region.effective
            ? "text-primary"
            : unavailable
              ? "text-warn"
              : "text-muted-foreground",
        )}
      >
        {t(`accounts.regionState.${region.state}` as MessageKey)}
      </span>
    </label>
  )
}
