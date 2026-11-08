import type { MessageKey, TFunction } from "@/i18n"

/** The API error body, api.md section 1.2. */
export type ApiError = {
  code: string
  message: string
  details?: Record<string, unknown>
}

function isApiError(e: unknown): e is ApiError {
  return (
    typeof e === "object" &&
    e !== null &&
    typeof (e as ApiError).code === "string"
  )
}

/** Localized one-line explanation for a failed request. */
export function errorText(t: TFunction, error: unknown): string {
  if (isApiError(error)) {
    const key = `error.${error.code}` as MessageKey
    const text = t(key)
    if (text !== key) return text
    return t("error.unknown")
  }
  if (error instanceof TypeError) return t("error.network")
  return t("error.unknown")
}
