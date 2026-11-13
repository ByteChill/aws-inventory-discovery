import { Link } from "@tanstack/react-router"
import { Check, LayoutDashboard, Search } from "lucide-react"
import { useDeferredValue, useState } from "react"

import { useAccountSearch, useApplicationSearch } from "@/api/hooks"
import { Dialog } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useI18n } from "@/i18n"
import type { Scope } from "@/lib/scope"
import { cn } from "@/lib/utils"

/**
 * The scope switcher: all applications and accounts, searchable one by one.
 * Choosing a scope only affects resource browsing (spec 1.2).
 */
export function ScopeDialog({
  current,
  onSelect,
  onClose,
}: {
  current: Scope | null
  onSelect: (scope: Scope | null) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const [q, setQ] = useState("")
  const deferred = useDeferredValue(q.trim())
  const apps = useApplicationSearch(deferred)
  const accounts = useAccountSearch(deferred)
  const searching = deferred.length > 0

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("scope.title")}
      className="w-[min(660px,calc(100vw-2rem))]"
    >
      <div className="flex items-center gap-2 rounded-md border border-input bg-card px-3 text-muted-foreground focus-within:outline-2 focus-within:outline-primary focus-within:outline-offset-1">
        <Search aria-hidden="true" className="size-4 shrink-0" />
        <Input
          autoFocus
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("scope.search")}
          aria-label={t("scope.search")}
          className="h-10 border-0 bg-transparent px-0 focus-visible:outline-0"
        />
      </div>

      <div className="flex max-h-[min(50vh,28rem)] flex-col gap-0.5 overflow-auto">
        {!searching && (
          <button
            type="button"
            onClick={() => onSelect(null)}
            className={cn(
              "grid grid-cols-[36px_minmax(0,1fr)_auto_16px] items-center gap-3 rounded-md px-2.5 py-2 text-left hover:bg-subtle",
              current === null && "bg-primary-soft",
            )}
          >
            <span
              aria-hidden="true"
              className="grid size-9 place-items-center rounded-md bg-subtle-strong text-muted-foreground"
            >
              <LayoutDashboard className="size-4" />
            </span>
            <span className="min-w-0">
              <strong className="block truncate font-semibold">
                {t("scope.all")}
              </strong>
            </span>
            {current === null && <SelectedMark />}
          </button>
        )}

        <SectionLabel
          label={t("scope.applications")}
          count={apps.data?.total}
        />
        {apps.data?.items.map((app) => {
          const selected = current?.kind === "app" && current.eim === app.eimId
          return (
            <button
              key={app.eimId}
              type="button"
              onClick={() => onSelect({ kind: "app", eim: app.eimId })}
              className={cn(
                "grid grid-cols-[36px_minmax(0,1fr)_auto_16px] items-center gap-3 rounded-md px-2.5 py-2 text-left hover:bg-subtle",
                selected && "bg-primary-soft",
              )}
            >
              <span
                aria-hidden="true"
                className="grid size-9 place-items-center rounded-md bg-subtle-strong font-semibold text-muted-foreground"
              >
                {app.eimApp.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0">
                <strong className="block truncate font-semibold">
                  {app.eimApp}
                </strong>
                <span className="block truncate font-mono text-xs text-muted-foreground">
                  {app.eimId}
                  {app.baId ? `，${app.baId}` : ""}
                </span>
              </span>
              <span className="text-xs text-muted-foreground">
                {app.environments
                  .map((e) => t(`env.${e}`))
                  .join(t("common.separator"))}
              </span>
              {selected && <SelectedMark />}
            </button>
          )
        })}
        {apps.data && apps.data.items.length === 0 && (
          <p className="px-2.5 py-1 text-muted-foreground">
            {t("scope.noApp")}
          </p>
        )}

        <SectionLabel
          label={t("scope.accounts")}
          count={accounts.data?.total}
        />
        {accounts.data?.items.map((account) => {
          const selected =
            current?.kind === "acct" && current.id === account.accountId
          return (
            <button
              key={account.accountId}
              type="button"
              onClick={() => onSelect({ kind: "acct", id: account.accountId })}
              className={cn(
                "grid grid-cols-[36px_minmax(0,1fr)_auto_16px] items-center gap-3 rounded-md px-2.5 py-2 text-left hover:bg-subtle",
                selected && "bg-primary-soft",
              )}
            >
              <span
                aria-hidden="true"
                className="grid size-9 place-items-center rounded-md bg-subtle-strong font-semibold text-muted-foreground"
              >
                {account.accountName.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0">
                <strong className="block truncate font-semibold">
                  {account.accountName}
                </strong>
                <span className="block truncate font-mono text-xs text-muted-foreground">
                  {account.accountId}
                </span>
              </span>
              <span className="text-xs text-muted-foreground">
                {[
                  account.eimApp ?? account.eimId,
                  t(`env.${account.accountType}`),
                ]
                  .filter(Boolean)
                  .join(t("common.separator"))}
              </span>
              {selected && <SelectedMark />}
            </button>
          )
        })}
        {accounts.data && accounts.data.items.length === 0 && (
          <p className="px-2.5 py-1 text-muted-foreground">
            {t("scope.noAccount")}
          </p>
        )}
      </div>

      <div className="flex flex-wrap gap-x-5 gap-y-1 border-t pt-3 text-primary">
        <Link to="/apps" onClick={onClose} className="hover:underline">
          {t("scope.allApps")}
        </Link>
        <Link to="/accounts" onClick={onClose} className="hover:underline">
          {t("scope.allAccounts")}
        </Link>
      </div>
    </Dialog>
  )
}

function SectionLabel({
  label,
  count,
}: {
  label: string
  count: number | undefined
}) {
  return (
    <p className="mt-3 mb-1 flex items-baseline gap-2 px-2.5 text-xs font-semibold text-muted-foreground">
      {label}
      {count !== undefined && <span className="font-normal">{count}</span>}
    </p>
  )
}

function SelectedMark() {
  return <Check className="size-4 text-primary" aria-hidden="true" />
}
