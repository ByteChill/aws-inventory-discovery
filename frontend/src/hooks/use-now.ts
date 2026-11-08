import { useEffect, useState } from "react"

const listeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | undefined

function subscribe(fn: () => void) {
  listeners.add(fn)
  if (!timer) {
    timer = setInterval(() => {
      for (const l of listeners) l()
    }, 30_000)
  }
  return () => {
    listeners.delete(fn)
    if (listeners.size === 0 && timer) {
      clearInterval(timer)
      timer = undefined
    }
  }
}

/** Current time, refreshed every 30 seconds so relative times stay right. */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => subscribe(() => setNow(Date.now())), [])
  return now
}
