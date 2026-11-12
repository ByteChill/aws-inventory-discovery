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
        <h1 className={cn("break-all", mono && "font-mono text-base")}>
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
      <div className="flex items-center justify-between gap-4">
        <h2>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  )
}
