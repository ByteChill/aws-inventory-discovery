import { client } from "@/client/client.gen"
import { basePath } from "@/lib/base-path"
import { getLocale } from "@/lib/preferences"

/**
 * One-time setup of the generated client: requests fail by throwing the API's
 * `{ code, message }` body (what `errorText` expects), and every request carries
 * the UI language and the caller id (api.md section 1). The API is reached under
 * the dashboard's own path prefix.
 */
export function setupApiClient() {
  client.setConfig({ baseUrl: basePath, throwOnError: true })
  client.interceptors.request.use((request) => {
    request.headers.set("Accept-Language", getLocale())
    request.headers.set("X-Caller-Id", "dashboard")
    return request
  })
}
