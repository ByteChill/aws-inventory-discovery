import { Link } from "@tanstack/react-router"
import { X } from "lucide-react"
import { type ReactNode, useEffect, useRef } from "react"

import type { Column, ResourceItem } from "@/client"
import { CopyButton } from "@/components/copy-button"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import { type Scope, scopeValue } from "@/lib/scope"

/**
 * The resource preview drawer (dashboard.md 3.5): configuration, ownership and
 * identity from the row already on screen, with the way into the full detail.
 * A native dialog gives the focus trap, Escape and focus restoration.
 */
export function ResourcePreview({
  resource,
  typeColumns,
  typeLabel,
  onClose,
}: {
  resource: ResourceItem | null
  /** The API's type columns, used as labels for the configuration rows. */
  typeColumns: Column[]
  typeLabel: string
  onClose: () => void
}) {
  const { t } = useI18n()
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (resource && !dialog.open) dialog.showModal()
    if (!resource && dialog.open) dialog.close()
  }, [resource])

  const account = resource?.account
  const name = resource?.resourceName ?? resource?.resourceId ?? ""

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: Escape is the dialog's native keyboard close
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      aria-label={t("preview.title")}
      className="m-0 ml-auto h-dvh max-h-dvh w-[min(520px,100vw)] rounded-none border-l bg-card p-0 text-foreground shadow-[var(--shadow-pop)] backdrop:bg-black/25"
    >
      {resource && (
        <div className="flex h-full flex-col">
          <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-1">
            <span className="rounded-sm border border-border bg-subtle px-1.5 py-0.5 text-xs text-muted-foreground">
              {typeLabel}
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t("preview.close")}
              onClick={() => ref.current?.close()}
            >
              <X />
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-auto px-6 pb-6">
            <h2 className="mt-2 break-all">{name}</h2>
            {name !== resource.resourceId && (
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <span className="truncate font-mono">
                  {resource.resourceId}
                </span>
                <CopyButton value={resource.resourceId} className="size-5" />
              </p>
            )}
            <div className="mt-2 flex flex-wrap gap-1.5">
              {resource.status !== "active" && (
                <Badge
                  tone={resource.status === "missing" ? "warn" : "neutral"}
                >
                  {t(`status.${resource.status}`)}
                </Badge>
              )}
              {resource.stale && <Badge>{t("status.stale")}</Badge>}
            </div>

            <PreviewSection title={t("preview.configuration")}>
              <ConfigRows resource={resource} columns={typeColumns} />
            </PreviewSection>

            <PreviewSection title={t("preview.ownership")}>
              <dl>
                <PreviewRow label={t("detail.account")}>
                  <ScopeLink
                    kind="acct"
                    value={account?.accountId ?? ""}
                    label={account?.accountName ?? account?.accountId ?? ""}
                  />
                  <span className="flex items-center gap-1 font-mono text-xs text-muted-foreground">
                    <span className="truncate">{account?.accountId}</span>
                    <CopyButton
                      value={account?.accountId ?? ""}
                      className="size-5"
                    />
                  </span>
                </PreviewRow>
                {account?.eimId && (
                  <PreviewRow label={t("preview.application")}>
                    <ScopeLink
                      kind="app"
                      value={account.eimId}
                      label={account.eimApp ?? account.eimId}
                    />
                    <span className="flex items-center gap-1 font-mono text-xs text-muted-foreground">
                      <span className="truncate">{account.eimId}</span>
                      <CopyButton value={account.eimId} className="size-5" />
                    </span>
                  </PreviewRow>
                )}
                <PreviewRow label={t("detail.env")}>
                  {account ? t(`env.${account.accountType}`) : null}
                </PreviewRow>
                <PreviewRow label={t("detail.region")}>
                  <span className="font-mono">
                    {resource.awsRegion === "global"
                      ? t("region.global")
                      : resource.awsRegion}
                  </span>
                </PreviewRow>
              </dl>
            </PreviewSection>

            <PreviewSection title={t("preview.identity")}>
              <dl>
                <PreviewRow label={t("detail.arn")}>
                  <span className="flex items-start gap-1">
                    <span className="break-all font-mono text-xs">
                      {resource.arn}
                    </span>
                    <CopyButton value={resource.arn} className="size-5" />
                  </span>
                </PreviewRow>
              </dl>
            </PreviewSection>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t px-6 py-4">
            <Button asChild>
              <Link to="/resources/$id" params={{ id: String(resource.id) }}>
                {t("preview.viewDetail")}
              </Link>
            </Button>
            <span className="text-xs text-muted-foreground">
              {t("preview.note")}
            </span>
          </div>
        </div>
      )}
    </dialog>
  )
}

function PreviewSection({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="mt-5">
      <h3 className="mb-1 text-xs font-semibold text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  )
}

function PreviewRow({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-3 border-b py-2 last:border-b-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  )
}

function ConfigRows({
  resource,
  columns,
}: {
  resource: ResourceItem
  columns: Column[]
}) {
  const { t } = useI18n()
  const labels = new Map(columns.map((c) => [c.key, c.label]))
  const entries = Object.entries(resource.attrs)
  if (entries.length === 0)
    return (
      <p className="text-xs text-muted-foreground">{t("preview.noConfig")}</p>
    )
  return (
    <dl>
      {entries.map(([key, value]) => (
        <div
          key={key}
          className="grid grid-cols-[96px_minmax(0,1fr)] gap-3 border-b py-2 last:border-b-0"
        >
          <dt className="text-xs text-muted-foreground">
            {labels.get(key) ?? key}
          </dt>
          <dd className="min-w-0 break-words text-sm">
            {formatValue(value, t)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function ScopeLink({
  kind,
  value,
  label,
}: {
  kind: "app" | "acct"
  value: string
  label: string
}) {
  if (!value) return null
  const scope: Scope =
    kind === "app" ? { kind: "app", eim: value } : { kind: "acct", id: value }
  return (
    <Link
      to="/"
      search={{ s: scopeValue(scope) }}
      className="block truncate text-sm text-primary hover:underline"
    >
      {label}
    </Link>
  )
}

function formatValue(
  value: unknown,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (value === null || value === undefined || value === "")
    return t("common.none")
  if (typeof value === "boolean")
    return value ? t("common.yes") : t("common.no")
  if (Array.isArray(value)) return value.map(String).join(t("common.separator"))
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}
