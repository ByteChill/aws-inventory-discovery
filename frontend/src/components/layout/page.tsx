import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export function Page({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "mx-auto flex w-full max-w-[1680px] flex-col gap-8 px-8 py-6 pb-12 max-[1180px]:px-5 max-[800px]:px-4",
        className,
      )}
    >
      {children}
    </div>
  )
}

export function PageHeader({
  title,
  description,
  mono,
  actions,
}: {
  title: ReactNode
  /** One line under the title: what this page shows, or the current scope. */
  description?: ReactNode
  /** Titles that are IDs or ARNs use the monospace face. */
  mono?: boolean
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        {/* IDs and ARNs have no spaces to wrap at; prose titles wrap by word. */}
        <h1
          className={cn("break-words", mono && "font-mono text-base break-all")}
        >
          {title}
        </h1>
        {description && (
          <p className="mt-1 text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      )}
    </div>
  )
}

/** A bordered card with a heading, the prototype's `.panel`. */
export function Panel({
  title,
  children,
  actions,
  className,
}: {
  title?: ReactNode
  children: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn("min-w-0 rounded-xl border bg-card px-5 py-4", className)}
    >
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}

/** The prototype's borderless summary strip: label above value, wrapping cells. */
export function Summary({
  children,
  label,
  className,
}: {
  children: ReactNode
  /** Accessible name for the strip. */
  label?: string
  className?: string
}) {
  return (
    <dl
      aria-label={label}
      className={cn(
        "grid grid-cols-2 gap-x-6 gap-y-4 rounded-xl border bg-card px-5 py-4",
        "sm:grid-cols-3 xl:grid-cols-4",
        className,
      )}
    >
      {children}
    </dl>
  )
}

export function SummaryItem({
  label,
  children,
}: {
  label: ReactNode
  children: ReactNode
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words">{children}</dd>
    </div>
  )
}

/** A titled block separated by whitespace and a divider, never a nested card. */
export function Section({
  title,
  children,
  actions,
}: {
  title: ReactNode
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    <section className="flex flex-col gap-3 border-t pt-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  )
}
