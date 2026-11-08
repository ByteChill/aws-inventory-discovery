import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useDelayedFlag } from "@/hooks/use-delayed-flag"
import { useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"

/** One line explaining a failed request, with a retry. Never a modal. */
export function InlineError({
  message,
  onRetry,
}: {
  message: string
  onRetry?: () => void
}) {
  const { t } = useI18n()
  return (
    <p role="alert" className="flex flex-wrap items-center gap-x-3 text-danger">
      <span>{message}</span>
      {onRetry && (
        <Button variant="link" size="sm" className="px-0" onClick={onRetry}>
          {t("action.retry")}
        </Button>
      )}
    </p>
  )
}

/** A short sentence plus the next step. */
export function EmptyState({
  message,
  action,
}: {
  message: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <p className="text-muted-foreground">{message}</p>
      {action}
    </div>
  )
}

/**
 * One block of a page: error line with retry, delayed skeleton, a one-line
 * empty message, or the content.
 */
export function QueryRegion({
  isLoading,
  error,
  onRetry,
  isEmpty,
  emptyMessage,
  lines,
  children,
}: {
  isLoading: boolean
  error: unknown
  onRetry?: () => void
  isEmpty: boolean
  emptyMessage: string
  lines?: number
  children?: ReactNode
}) {
  const { t } = useI18n()
  if (error)
    return <InlineError message={errorText(t, error)} onRetry={onRetry} />
  if (isLoading) return <LoadingBlock loading lines={lines} />
  if (isEmpty) return <p className="text-muted-foreground">{emptyMessage}</p>
  return <>{children}</>
}

/** Skeleton lines, shown only after the request has taken longer than 300 ms. */
export function LoadingBlock({
  loading,
  lines = 3,
}: {
  loading: boolean
  lines?: number
}) {
  const visible = useDelayedFlag(loading)
  if (!visible) return null
  return (
    <div className="flex flex-col gap-2" role="status" aria-busy="true">
      {Array.from({ length: lines }, (_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders
        <Skeleton key={i} className="h-5 w-full max-w-md" />
      ))}
    </div>
  )
}
