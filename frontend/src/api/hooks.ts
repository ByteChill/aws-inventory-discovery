import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
} from "@tanstack/react-query"

import {
  getAccount,
  getCoverage,
  getRelationships,
  getResource,
  type ListAccountsData,
  type ListApplicationsData,
  type ListResourcesData,
  listAccounts,
  listApplications,
  listColumns,
  listResources,
  listResourceTypes,
  lookupResource,
  overview,
  type ResourceFacetsData,
  type ResourceSummaryData,
  resourceFacets,
  resourceSummary,
} from "@/client"
import type { Column } from "@/components/data-table"
import { useI18n } from "@/i18n"
import type { Params } from "@/lib/resource-query"

// Every call throws the API's error body (see client.ts), so TanStack Query's
// `error` is what `errorText` reads. Calls whose text depends on the language
// (labels from the API) carry the locale in their key.

type ListQuery = NonNullable<ListResourcesData["query"]>
type FacetQuery = NonNullable<ResourceFacetsData["query"]>
type SummaryQuery = NonNullable<ResourceSummaryData["query"]>
type AccountQuery = NonNullable<ListAccountsData["query"]>
type ApplicationQuery = NonNullable<ListApplicationsData["query"]>

/** Columns for the resource list: the common ones, plus the type's when one is chosen. */
export function useColumns(resourceType: string | undefined) {
  const { locale } = useI18n()
  return useQuery({
    queryKey: ["columns", locale, resourceType ?? null],
    queryFn: async () =>
      (await listColumns({ query: { resourceType }, throwOnError: true }))
        .data as Column[],
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  })
}

export function useResourceTypes() {
  const { locale } = useI18n()
  return useQuery({
    queryKey: ["resource-types", locale],
    queryFn: async () => (await listResourceTypes({ throwOnError: true })).data,
    staleTime: 5 * 60_000,
  })
}

export function useResources(
  params: Params,
  paging: { page: number; pageSize: number; sort: string | undefined },
  enabled = true,
) {
  return useQuery({
    queryKey: ["resources", params, paging],
    queryFn: async () =>
      (
        await listResources({
          query: {
            ...params,
            page: paging.page,
            pageSize: paging.pageSize,
            sort: paging.sort,
          } as ListQuery,
          throwOnError: true,
        })
      ).data,
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useFacets(fields: string[], params: Params, enabled = true) {
  const { locale } = useI18n()
  return useQuery({
    queryKey: ["facets", locale, fields, params],
    queryFn: async () =>
      (
        await resourceFacets({
          query: { ...params, field: fields } as FacetQuery,
          throwOnError: true,
        })
      ).data,
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useResourceSummary(params: Params, enabled = true) {
  return useQuery({
    queryKey: ["resource-summary", params],
    queryFn: async () =>
      (
        await resourceSummary({
          query: params as SummaryQuery,
          throwOnError: true,
        })
      ).data,
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useResource(id: number | undefined) {
  return useQuery({
    queryKey: ["resource", id],
    queryFn: async () =>
      (
        await getResource({
          path: { resource_id: id as number },
          throwOnError: true,
        })
      ).data,
    enabled: id !== undefined,
  })
}

/** All rows for an ARN: one, except for a shared AMI that shows up in several accounts. */
export function useLookup(arn: string | undefined) {
  return useQuery({
    queryKey: ["lookup", arn],
    queryFn: async () =>
      (
        await lookupResource({
          query: { arn: arn as string },
          throwOnError: true,
        })
      ).data,
    enabled: arn !== undefined,
  })
}

export function useRelationships(id: number) {
  return useInfiniteQuery({
    queryKey: ["relationships", id],
    initialPageParam: 1,
    queryFn: async ({ pageParam }) =>
      (
        await getRelationships({
          path: { resource_id: id },
          query: { direction: "both", page: pageParam, pageSize: 100 },
          throwOnError: true,
        })
      ).data,
    getNextPageParam: (last) =>
      last.page * last.pageSize < last.total ? last.page + 1 : undefined,
  })
}

export function useOverview() {
  const { locale } = useI18n()
  return useQuery({
    queryKey: ["overview", locale],
    queryFn: async () => (await overview({ throwOnError: true })).data,
  })
}

export function useAccounts(
  params: Record<string, string | string[] | boolean>,
  paging: { page: number; pageSize: number; sort: string | undefined },
  enabled = true,
) {
  return useQuery({
    queryKey: ["accounts", params, paging],
    queryFn: async () =>
      (
        await listAccounts({
          query: {
            ...params,
            page: paging.page,
            pageSize: paging.pageSize,
            sort: paging.sort,
          } as AccountQuery,
          throwOnError: true,
        })
      ).data,
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useAccount(accountId: string | undefined) {
  return useQuery({
    queryKey: ["account", accountId],
    queryFn: async () =>
      (
        await getAccount({
          path: { account_id: accountId as string },
          throwOnError: true,
        })
      ).data,
    enabled: accountId !== undefined,
  })
}

export function useCoverage(accountId: string) {
  return useQuery({
    queryKey: ["coverage", accountId],
    queryFn: async () =>
      (
        await getCoverage({
          path: { account_id: accountId },
          throwOnError: true,
        })
      ).data,
  })
}

/** Applications (one row per EIM), filtered and paged by the API. */
export function useApplications(
  query: { q?: string; accountType?: string },
  paging: { page: number; pageSize: number; sort?: string },
) {
  return useQuery({
    queryKey: ["applications", query, paging],
    queryFn: async () =>
      (
        await listApplications({
          query: {
            ...query,
            page: paging.page,
            pageSize: paging.pageSize,
            sort: paging.sort,
          } as ApplicationQuery,
          throwOnError: true,
        })
      ).data,
    placeholderData: keepPreviousData,
  })
}

/** The applications matching a search, for the scope switcher. */
export function useApplicationSearch(q: string) {
  return useQuery({
    queryKey: ["applications-search", q],
    queryFn: async () =>
      (
        await listApplications({
          query: { q, pageSize: 20 } as ApplicationQuery,
          throwOnError: true,
        })
      ).data,
  })
}

/** The application behind an EIM in the URL scope. */
export function useApplication(eim: string | undefined) {
  return useQuery({
    queryKey: ["application", eim],
    queryFn: async () => {
      const data = (
        await listApplications({
          query: { q: eim, pageSize: 20 } as ApplicationQuery,
          throwOnError: true,
        })
      ).data
      return data.items.find((a) => a.eimId === eim) ?? null
    },
    enabled: eim !== undefined,
    staleTime: 5 * 60_000,
  })
}

/** Account and application search for the scope switcher. */
export function useAccountSearch(q: string) {
  return useQuery({
    queryKey: ["accounts-search", q],
    queryFn: async () =>
      (
        await listAccounts({
          query: { q, pageSize: 20 } as AccountQuery,
          throwOnError: true,
        })
      ).data,
  })
}
