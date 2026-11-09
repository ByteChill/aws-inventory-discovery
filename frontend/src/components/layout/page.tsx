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
    <div className={cn("flex flex-col gap-8 px-6 py-6", className)}>
      {children}
    </div>
  )
}

export function PageHeader({
  title,
  mono,
  actions,
}: {
  title: ReactNode
  /** Titles that are IDs or ARNs use the monospace face. */
  mono?: boolean
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
      <h1 className={cn("min-w-0 break-all", mono && "font-mono text-base")}>
        {title}
      </h1>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
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
