import { Check, Copy } from "lucide-react"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

export function CopyButton({
  value,
  className,
  text,
}: {
  value: string
  className?: string
  /** Show this label beside the icon instead of an icon-only button. */
  text?: string
}) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const id = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(id)
  }, [copied])

  const label = copied ? t("action.copied") : (text ?? t("action.copy"))
  return (
    <Button
      variant={text ? "outline" : "ghost"}
      size={text ? "default" : "icon"}
      className={cn(
        text ? "gap-1.5" : "size-6 text-muted-foreground",
        className,
      )}
      aria-label={label}
      title={label}
      onClick={() => {
        navigator.clipboard?.writeText(value).then(() => setCopied(true))
      }}
    >
      {copied ? <Check /> : <Copy />}
      {text && <span>{label}</span>}
    </Button>
  )
}
