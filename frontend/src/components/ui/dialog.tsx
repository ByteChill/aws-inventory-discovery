import { type ReactNode, useEffect, useRef } from "react"

import { cn } from "@/lib/utils"

/**
 * A modal on the native <dialog> element: focus trap, Escape and the backdrop
 * come from the browser. It is the one floating panel with a shadow.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  className,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  className?: string
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open && !el.open) el.showModal()
    if (!open && el.open) el.close()
  }, [open])

  return (
    // Escape closes it natively; a stray click outside does not, so a filled
    // form is never lost. Pinned near the top so filtering a list inside it
    // does not move the controls.
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={title}
      className={cn(
        "mx-auto mt-[6vh] max-h-[88vh] w-[min(32rem,calc(100vw-2rem))] overflow-auto rounded-lg border bg-card p-5 text-foreground shadow-[var(--shadow-float)] backdrop:bg-black/30",
        className,
      )}
    >
      {open && (
        <div className="flex flex-col gap-4">
          <h2>{title}</h2>
          {children}
        </div>
      )}
    </dialog>
  )
}
