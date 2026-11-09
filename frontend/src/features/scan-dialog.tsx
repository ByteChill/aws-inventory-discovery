import { useNavigate } from "@tanstack/react-router"
import { useEffect, useMemo, useState } from "react"

import { useFacets, useResourceTypes } from "@/api/hooks"
import { newIdempotencyKey, useAllAccounts, useCreateScan } from "@/api/scans"
import { InlineError } from "@/components/inline-state"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatCount } from "@/lib/format"
import { cn } from "@/lib/utils"

const MAX_ACCOUNTS = 50

type Item = { value: string; label: string; detail?: string }

/** A checkbox list in a bordered box, optionally searchable. */
function CheckList({
  items,
  selected,
  onChange,
  search,
  searchPlaceholder,
  empty,
  disabled,
}: {
  items: Item[]
  selected: Set<string>
  onChange: (next: Set<string>) => void
  search?: (item: Item, q: string) => boolean
  searchPlaceholder?: string
  empty: string
  disabled?: boolean
}) {
  const [q, setQ] = useState("")
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return needle && search ? items.filter((i) => search(i, needle)) : items
  }, [items, q, search])
  return (
    <div
      className={cn(
        "flex flex-col rounded-lg border bg-card",
        disabled && "opacity-50",
      )}
    >
      {search && (
        <input
          type="search"
          value={q}
          disabled={disabled}
          onChange={(e) => setQ(e.target.value)}
          placeholder={searchPlaceholder}
          aria-label={searchPlaceholder}
          className="h-8 rounded-t-lg border-b bg-transparent px-2.5 placeholder:text-muted-foreground"
        />
      )}
      <ul className="max-h-40 overflow-auto p-1">
        {shown.length === 0 && (
          <li className="px-2 py-1 text-muted-foreground">{empty}</li>
        )}
        {shown.map((item) => (
          <li key={item.value}>
            <label className="flex items-center gap-2 rounded-sm px-2 py-1 hover:bg-subtle">
              <input
                type="checkbox"
                disabled={disabled}
                checked={selected.has(item.value)}
                onChange={(e) => {
                  const next = new Set(selected)
                  if (e.target.checked) next.add(item.value)
                  else next.delete(item.value)
                  onChange(next)
                }}
                className="accent-[var(--accent)]"
              />
              <span>{item.label}</span>
              {item.detail && (
                <span className="ml-auto font-mono text-muted-foreground">
                  {item.detail}
                </span>
              )}
            </label>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** The form behind the 扫描 button on the scans page. */
export function ScanDialog({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const accounts = useAllAccounts(open)
  const facets = useFacets(["awsRegion"], {}, open)
  const types = useResourceTypes()
  const create = useCreateScan()

  const [all, setAll] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [regions, setRegions] = useState<Set<string>>(new Set())
  const [resourceTypes, setResourceTypes] = useState<Set<string>>(new Set())
  const [key, setKey] = useState(newIdempotencyKey)

  // A fresh form, and a fresh idempotency key, every time the dialog opens.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on open only
  useEffect(() => {
    if (!open) return
    setAll(false)
    setPicked(new Set())
    setRegions(new Set())
    setResourceTypes(new Set())
    setKey(newIdempotencyKey())
    create.reset()
  }, [open])

  const accountItems = useMemo<Item[]>(
    () =>
      (accounts.data ?? []).map((a) => ({
        value: a.accountId,
        label: a.accountName,
        detail: a.accountId,
        // Searchable text beyond the label and ID.
        search: [a.eimId, a.eimApp, a.baId].filter(Boolean).join(" "),
      })) as Item[],
    [accounts.data],
  )
  const regionItems = useMemo<Item[]>(
    () =>
      (facets.data?.awsRegion ?? []).map((b) => ({
        value: b.value,
        label: b.value,
      })),
    [facets.data],
  )
  const typeItems = useMemo<Item[]>(
    () =>
      (types.data ?? []).map((x) => ({
        value: x.typeName,
        label: x.label,
        detail: x.typeName,
      })),
    [types.data],
  )

  const accountCount = all ? (accounts.data?.length ?? 0) : picked.size
  const regionCount = regions.size || regionItems.length
  const typeCount = resourceTypes.size || typeItems.length
  const tooMany = !all && picked.size > MAX_ACCOUNTS
  const canSubmit = (all || picked.size > 0) && !tooMany && !create.isPending

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
          navigate({
            to: "/scans/$id",
            params: { id: String(scan.id) },
          })
        },
      },
    )
  }

  return (
    <Dialog open={open} onClose={onClose} title={t("action.scan")}>
      <div className="flex flex-col gap-4">
        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1.5">{t("scans.form.accounts")}</legend>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={all}
              onChange={(e) => setAll(e.target.checked)}
              className="accent-[var(--accent)]"
            />
            {t("scans.form.allAccounts")}
          </label>
          <CheckList
            items={accountItems}
            selected={picked}
            onChange={setPicked}
            disabled={all}
            searchPlaceholder={t("scans.form.accountsSearch")}
            empty={t("scans.form.noAccounts")}
            search={(i, q) =>
              `${i.label} ${i.detail ?? ""} ${(i as Item & { search?: string }).search ?? ""}`
                .toLowerCase()
                .includes(q)
            }
          />
        </fieldset>
        {!all && (
          <>
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1.5">
                {t("scans.form.regions")}
                {regions.size === 0 && (
                  <span className="ml-2 text-muted-foreground">
                    {t("scans.form.allRegions")}
                  </span>
                )}
              </legend>
              <CheckList
                items={regionItems}
                selected={regions}
                onChange={setRegions}
                empty={t("scans.form.noAccounts")}
              />
            </fieldset>
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1.5">
                {t("scans.form.types")}
                {resourceTypes.size === 0 && (
                  <span className="ml-2 text-muted-foreground">
                    {t("scans.form.allTypes")}
                  </span>
                )}
              </legend>
              <CheckList
                items={typeItems}
                selected={resourceTypes}
                onChange={setResourceTypes}
                empty={t("scans.form.noAccounts")}
              />
            </fieldset>
          </>
        )}
        {create.error && <InlineError message={errorText(t, create.error)} />}
        <div className="flex flex-col gap-2 border-t pt-4">
          <p>
            {t("scans.form.scope", {
              accounts: formatCount(t, "accounts", accountCount),
              regions: formatCount(
                t,
                "regions",
                all ? regionItems.length : regionCount,
              ),
              types: formatCount(
                t,
                "types",
                all ? typeItems.length : typeCount,
              ),
            })}
          </p>
          <div className="flex items-center justify-end gap-3">
            {tooMany && (
              <span className="text-muted-foreground">
                {t("scans.form.limit")}
              </span>
            )}
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
