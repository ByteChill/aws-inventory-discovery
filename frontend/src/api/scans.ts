import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import {
  type AccountSummary,
  cancelScan,
  confirmScopeChange,
  createScan,
  getScan,
  getTask,
  getTaskLogs,
  type ListResourcesData,
  type ListScansData,
  listAccounts,
  listResources,
  listScanScopes,
  listScans,
  listScanTasks,
  listScopeCandidates,
  type RefreshedResource,
  refreshResource,
  retryFailed,
  type ScanSpec,
  scanSummary,
} from "@/client"

type ScansQuery = NonNullable<ListScansData["query"]>
type TasksQuery = NonNullable<Parameters<typeof listScanTasks>[0]["query"]>

// Status blocks poll so a running scan moves on its own.

export function useScanSummary() {
  return useQuery({
    queryKey: ["scan-summary"],
    queryFn: async () => (await scanSummary({ throwOnError: true })).data,
    refetchInterval: 5_000,
  })
}

export function useScans(
  params: { trigger?: string; status?: string },
  paging: { page: number; pageSize: number },
) {
  return useQuery({
    queryKey: ["scans", params, paging],
    queryFn: async () =>
      (
        await listScans({
          query: { ...params, ...paging } as ScansQuery,
          throwOnError: true,
        })
      ).data,
    placeholderData: keepPreviousData,
    refetchInterval: 5_000,
  })
}

export function useScan(id: number) {
  return useQuery({
    queryKey: ["scan", id],
    queryFn: async () =>
      (await getScan({ path: { scan_id: id }, throwOnError: true })).data,
    refetchInterval: (q) => {
      const status = q.state.data?.status
      return status === "queued" || status === "running" ? 3_000 : false
    },
  })
}

export function useScanTasks(
  id: number,
  params: Record<string, string>,
  paging: { page: number; pageSize: number },
) {
  return useQuery({
    queryKey: ["scan-tasks", id, params, paging],
    queryFn: async () =>
      (
        await listScanTasks({
          path: { scan_id: id },
          query: { ...params, ...paging } as TasksQuery,
          throwOnError: true,
        })
      ).data,
    placeholderData: keepPreviousData,
    refetchInterval: 5_000,
  })
}

export function useTask(id: number) {
  return useQuery({
    queryKey: ["task", id],
    queryFn: async () =>
      (await getTask({ path: { task_id: id }, throwOnError: true })).data,
  })
}

export function useTaskLogs(id: number) {
  return useInfiniteQuery({
    queryKey: ["task-logs", id],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) =>
      (
        await getTaskLogs({
          path: { task_id: id },
          query: { limit: 200, nextToken: pageParam },
          throwOnError: true,
        })
      ).data,
    getNextPageParam: (last) => last.nextToken ?? undefined,
    retry: false,
  })
}

export function useAbnormalScopes() {
  return useQuery({
    queryKey: ["scan-scopes"],
    queryFn: async () =>
      (await listScanScopes({ query: { pageSize: 200 }, throwOnError: true }))
        .data,
    refetchInterval: 15_000,
  })
}

/** The resources a guard spared in one scope, as saved when it tripped. */
export function useScopeCandidates(
  scanShardId: number | undefined,
  q: string | undefined,
  paging: { page: number; pageSize: number },
) {
  return useQuery({
    queryKey: ["scope-candidates", scanShardId, q, paging],
    queryFn: async () =>
      (
        await listScopeCandidates({
          path: { scan_shard_id: scanShardId as number },
          query: { q, ...paging },
          throwOnError: true,
        })
      ).data,
    enabled: scanShardId !== undefined,
    placeholderData: keepPreviousData,
  })
}

export function useMissingResources(enabled: boolean) {
  return useQuery({
    queryKey: ["missing-resources"],
    queryFn: async () =>
      (
        await listResources({
          query: {
            status: ["missing"],
            pageSize: 50,
            sort: "-missingSince",
          } as NonNullable<ListResourcesData["query"]>,
          throwOnError: true,
        })
      ).data,
    enabled,
    refetchInterval: 15_000,
  })
}

/** Every account, for the scan form's search; small enough to hold in memory. */
export function useAllAccounts(enabled: boolean) {
  return useQuery({
    queryKey: ["accounts-all"],
    enabled,
    staleTime: 60_000,
    queryFn: async () => {
      const out: AccountSummary[] = []
      for (let page = 1; ; page++) {
        const data = (
          await listAccounts({
            query: { page, pageSize: 200 },
            throwOnError: true,
          })
        ).data
        out.push(...data.items)
        if (page * data.pageSize >= data.total) return out
      }
    },
  })
}

// Writes

/** One id per opening of the scan form, so a double submit is one scan. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto)
    return crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function useCreateScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ spec, key }: { spec: ScanSpec; key?: string }) =>
      (
        await createScan({
          body: spec,
          headers: key ? { "idempotency-key": key } : undefined,
          throwOnError: true,
        })
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scans"] })
      qc.invalidateQueries({ queryKey: ["scan-summary"] })
    },
  })
}

export function useRetryFailed(id: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      (await retryFailed({ path: { scan_id: id }, throwOnError: true })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scan", id] })
      qc.invalidateQueries({ queryKey: ["scan-tasks", id] })
    },
  })
}

export function useCancelScan(id: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      (await cancelScan({ path: { scan_id: id }, throwOnError: true })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scan", id] })
      qc.invalidateQueries({ queryKey: ["scan-tasks", id] })
    },
  })
}

export function useConfirmChange() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: {
      accountId: string
      awsRegion: string
      resourceType: string
    }) => (await confirmScopeChange({ body, throwOnError: true })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scan-scopes"] })
      qc.invalidateQueries({ queryKey: ["scan-summary"] })
      qc.invalidateQueries({ queryKey: ["scans"] })
    },
  })
}

export type RefreshKey = {
  accountId: string
  awsRegion: string
  resourceType: string
  resourceId: string
}

export type RefreshPhase = "idle" | "waiting" | "queued"

const DONE = new Set(["succeeded", "failed", "cancelled"])

/**
 * Refresh one resource: wait up to 20 s on the server; on 202 poll the task,
 * then refetch whatever shows the resource. `error` is the localizable reason
 * (an error code) from `refreshError`, a failed task or a failed request.
 */
export function useRefreshResource() {
  const qc = useQueryClient()
  const [phase, setPhase] = useState<RefreshPhase>("idle")
  const [error, setError] = useState<unknown>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  function refetchAll() {
    for (const key of ["resource", "lookup", "resources", "missing-resources"])
      qc.invalidateQueries({ queryKey: [key] })
    qc.invalidateQueries({ queryKey: ["scan-summary"] })
  }

  async function run(key: RefreshKey) {
    setError(null)
    setPhase("waiting")
    try {
      const { data } = await refreshResource({
        body: key,
        query: { wait: 20 },
        throwOnError: true,
      })
      if ("taskId" in data) {
        setPhase("queued")
        for (;;) {
          await new Promise((r) => setTimeout(r, 1500))
          if (!alive.current) return
          const task = (
            await getTask({
              path: { task_id: data.taskId },
              throwOnError: true,
            })
          ).data
          if (!DONE.has(task.status)) continue
          if (task.status === "failed" && task.errorCode)
            setError({ code: task.errorCode })
          break
        }
      } else {
        const resource = data as RefreshedResource
        if (resource.refreshError) setError(resource.refreshError)
        qc.setQueryData(["resource", resource.id], resource)
      }
      refetchAll()
    } catch (e) {
      setError(e)
    } finally {
      if (alive.current) setPhase("idle")
    }
  }

  return { run, phase, error }
}
