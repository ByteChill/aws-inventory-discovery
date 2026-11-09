import { Check, Copy } from "lucide-react"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

export function CopyButton({
  value,
  className,
}: {
  value: string
  className?: string
}) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const id = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(id)
  }, [copied])

  const label = copied ? t("action.copied") : t("action.copy")
  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn("size-6 text-muted-foreground", className)}
      aria-label={label}
      title={label}
      onClick={() => {
        navigator.clipboard?.writeText(value).then(() => setCopied(true))
      }}
    >
      {copied ? <Check /> : <Copy />}
    </Button>
  )
}
