import { client } from "@/client/client.gen"
import { getLocale } from "@/lib/preferences"

/**
 * One-time setup of the generated client: requests fail by throwing the API's
 * `{ code, message }` body (what `errorText` expects), and every request carries
 * the UI language and the caller id (api.md section 1).
 */
export function setupApiClient() {
  client.setConfig({ throwOnError: true })
  client.interceptors.request.use((request) => {
    request.headers.set("Accept-Language", getLocale())
    request.headers.set("X-Caller-Id", "dashboard")
    return request
  })
}
