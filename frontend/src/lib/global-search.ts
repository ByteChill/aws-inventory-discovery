import {
  getScan,
  getTask,
  listAccounts,
  listApplications,
  listResources,
} from "@/client"

/**
 * Where a global search should go (spec 1.3): an ARN or a unique resource ID
 * opens the resource, an application or account switches scope, a scan or task
 * number opens that page, and anything else is a resource search.
 */
export type SearchTarget =
  | { kind: "resource"; id: string }
  | { kind: "scope"; s: string }
  | { kind: "scan"; id: number }
  | { kind: "task"; id: number }
  | { kind: "list"; q: string }

const ARN = /^arn:aws[\w-]*:/i

export async function resolveGlobalSearch(
  input: string,
): Promise<SearchTarget> {
  const q = input.trim()
  if (ARN.test(q)) return { kind: "resource", id: q }

  // An exact resource ID or name wins; a contains match goes to the list.
  const matches = await listResources({
    query: { q, pageSize: 20 },
    throwOnError: false,
  })
  const exact = (matches.data?.items ?? []).filter(
    (r) => r.resourceId === q || r.resourceName === q,
  )
  if (exact.length === 1) return { kind: "resource", id: String(exact[0].id) }

  const number = Number(q.replace(/^#/, ""))
  if (Number.isInteger(number) && number > 0) {
    const scan = await getScan({
      path: { scan_id: number },
      throwOnError: false,
    })
    if (scan.data) return { kind: "scan", id: number }
    const task = await getTask({
      path: { task_id: number },
      throwOnError: false,
    })
    if (task.data) return { kind: "task", id: number }
  }

  const [apps, accounts] = await Promise.all([
    listApplications({
      query: { q, pageSize: 20 },
      throwOnError: false,
    }),
    listAccounts({ query: { q, pageSize: 20 }, throwOnError: false }),
  ])

  const lower = q.toLowerCase()
  const app = apps.data?.items.find(
    (a) => a.eimId === q || a.eimApp.toLowerCase() === lower,
  )
  if (app) return { kind: "scope", s: `app:${app.eimId}` }
  const account = accounts.data?.items.find(
    (a) => a.accountId === q || a.accountName.toLowerCase() === lower,
  )
  if (account) return { kind: "scope", s: `acct:${account.accountId}` }

  return { kind: "list", q }
}
