import { useNow } from "@/hooks/use-now"
import { type MessageKey, useI18n } from "@/i18n"
import { formatExactTime, formatRelativeTime } from "@/lib/format"

/** Relative time ("3 分钟前"); hover shows the exact time, and a note if given. */
export function Time({
  value,
  note,
  className,
}: {
  value: string | null | undefined
  /** Extra line in the hover text, e.g. where the data came from. */
  note?: string
  className?: string
}) {
  const { t, locale } = useI18n()
  const now = useNow()
  if (!value) return null
  const exact = formatExactTime(value, locale)
  return (
    <time
      dateTime={value}
      title={note ? `${exact}\n${note}` : exact}
      className={className}
    >
      {formatRelativeTime(value, t, now)}
    </time>
  )
}

const SLOT = "\u0000"

/** A sentence around a time: "更新于 3 分钟前" from the message "更新于 {time}". */
export function TimeSentence({
  message,
  value,
  note,
}: {
  message: MessageKey
  value: string | null | undefined
  note?: string
}) {
  const { t } = useI18n()
  const [before, after = ""] = t(message, { time: SLOT }).split(SLOT)
  return (
    <span>
      {before}
      <Time value={value} note={note} />
      {after}
    </span>
  )
}
