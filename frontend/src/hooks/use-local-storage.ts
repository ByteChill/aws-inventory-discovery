import { useCallback, useState } from "react"
import { readStorage, writeStorage } from "@/lib/preferences"

/** JSON value remembered in localStorage; falls back silently if unavailable. */
export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    const raw = readStorage(key)
    if (raw === null) return initial
    try {
      return JSON.parse(raw) as T
    } catch {
      return initial
    }
  })
  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const resolved =
          typeof next === "function" ? (next as (p: T) => T)(prev) : next
        writeStorage(key, JSON.stringify(resolved))
        return resolved
      })
    },
    [key],
  )
  return [value, set] as const
}
