import { useNow } from "@/hooks/use-now"
import { useI18n } from "@/i18n"
import { formatExactTime, formatRelativeTime } from "@/lib/format"

/** Relative time ("3 分钟前"); hover shows the exact time. */
export function Time({
  value,
  className,
}: {
  value: string | null | undefined
  className?: string
}) {
  const { t, locale } = useI18n()
  const now = useNow()
  if (!value) return null
  return (
    <time
      dateTime={value}
      title={formatExactTime(value, locale)}
      className={className}
    >
      {formatRelativeTime(value, t, now)}
    </time>
  )
}
