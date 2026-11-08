import { useEffect, useState } from "react"

/** True only after `flag` has been true for `delayMs` (default 300 ms). */
export function useDelayedFlag(flag: boolean, delayMs = 300): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!flag) {
      setShown(false)
      return
    }
    const id = setTimeout(() => setShown(true), delayMs)
    return () => clearTimeout(id)
  }, [flag, delayMs])
  return flag && shown
}
