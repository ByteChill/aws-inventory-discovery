import { randomUUID } from "node:crypto"
import { request } from "@playwright/test"
import { BASE_URL } from "./base-url"

const TIMEOUT_MS = 10 * 60_000

/**
 * Starts a full on-demand scan through the API and waits for it, so every test sees data the
 * stack itself produced (the seed loads the simulated AWS accounts; the scan reads them).
 */
export default async function globalSetup() {
  const api = await request.newContext({ baseURL: BASE_URL })
  // Account master data is local now. A fresh stack starts empty, so the setup imports the
  // simulated registry once; an existing stack keeps its local edits.
  const accounts = await api.get("api/v1/accounts?pageSize=1")
  if (!accounts.ok())
    throw new Error(
      `GET api/v1/accounts failed: ${accounts.status()} ${await accounts.text()}`,
    )
  const accountPage = (await accounts.json()) as { total: number }
  if (accountPage.total === 0) {
    const imported = await api.post("api/v1/accounts/import")
    if (!imported.ok())
      throw new Error(
        `POST api/v1/accounts/import failed: ${imported.status()} ${await imported.text()}`,
      )
  }
  const created = await api.post("api/v1/scans", {
    headers: { "idempotency-key": randomUUID() },
    data: { all: true },
  })
  if (!created.ok())
    throw new Error(
      `POST api/v1/scans failed: ${created.status()} ${await created.text()}`,
    )
  const { id } = (await created.json()) as { id: number }

  const deadline = Date.now() + TIMEOUT_MS
  for (;;) {
    const scan = (await (await api.get(`api/v1/scans/${id}`)).json()) as {
      status: string
    }
    if (scan.status === "succeeded") break
    if (["failed", "partial", "cancelled"].includes(scan.status))
      throw new Error(`setup scan ${id} ended as ${scan.status}`)
    if (Date.now() > deadline)
      throw new Error(`setup scan ${id} still ${scan.status} after 10 min`)
    await new Promise((r) => setTimeout(r, 2000))
  }
  await api.dispose()
}
