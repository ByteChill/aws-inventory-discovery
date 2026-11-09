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
  type ListResourcesData,
  listAccounts,
  listColumns,
  listResources,
  listResourceTypes,
  lookupResource,
  overview,
  type ResourceFacetsData,
  resourceFacets,
} from "@/client"
import type { Column } from "@/components/data-table"
import { useI18n } from "@/i18n"
import type { Params } from "@/lib/resource-query"

// Every call throws the API's error body (see client.ts), so TanStack Query's
// `error` is what `errorText` reads. Calls whose text depends on the language
// (labels from the API) carry the locale in their key.

type ListQuery = NonNullable<ListResourcesData["query"]>
type FacetQuery = NonNullable<ResourceFacetsData["query"]>
type AccountQuery = NonNullable<ListAccountsData["query"]>

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
  })
}

export function useAccount(accountId: string) {
  return useQuery({
    queryKey: ["account", accountId],
    queryFn: async () =>
      (
        await getAccount({
          path: { account_id: accountId },
          throwOnError: true,
        })
      ).data,
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
