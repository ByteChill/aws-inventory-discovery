import { cva, type VariantProps } from "class-variance-authority"
import type * as React from "react"

import { cn } from "@/lib/utils"

// Badges are for abnormal states only, so they stay small and quiet.
const badgeVariants = cva(
  "inline-flex shrink-0 items-center whitespace-nowrap rounded-sm px-1.5 text-xs font-medium",
  {
    variants: {
      tone: {
        warn: "bg-warn-soft text-warn",
        neutral: "bg-subtle-strong text-muted-foreground",
        danger: "bg-danger-soft text-danger",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
)

function Badge({
  className,
  tone,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />
}

export { Badge }
