import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import {
  createReport,
  deleteReport,
  getReport,
  listReports,
  type ReportInput,
  reportFields,
  reportResults,
  reportsOverview,
  updateReport,
} from "@/client"
import { useI18n } from "@/i18n"

/** Saved reports and their current results. Nothing here writes inventory (D-36). */

export function useReports() {
  return useQuery({
    queryKey: ["reports"],
    queryFn: async () => (await listReports({ throwOnError: true })).data,
  })
}

/** Matching counts per enabled report, computed when the page opens. */
export function useReportsOverview(scope: string | undefined) {
  const { locale } = useI18n()
  return useQuery({
    queryKey: ["reports", "overview", locale, scope ?? null],
    queryFn: async () =>
      (await reportsOverview({ query: { s: scope }, throwOnError: true })).data,
    placeholderData: keepPreviousData,
    refetchOnWindowFocus: true,
  })
}

export function useReport(id: number | undefined) {
  return useQuery({
    queryKey: ["report", id],
    queryFn: async () =>
      (
        await getReport({
          path: { report_id: id as number },
          throwOnError: true,
        })
      ).data,
    enabled: id !== undefined,
  })
}

/** The fields a condition may name, for the editor's selects. */
export function useReportFields(subjectType: string | undefined) {
  const { locale } = useI18n()
  return useQuery({
    queryKey: ["reports", "fields", locale, subjectType ?? null],
    queryFn: async () =>
      (
        await reportFields({
          query: { subjectType },
          throwOnError: true,
        })
      ).data,
    staleTime: 5 * 60_000,
  })
}

export function useReportResults(
  id: number,
  paging: { page: number; pageSize: number; sort: string | undefined },
  scope: string | undefined,
) {
  const { locale } = useI18n()
  return useQuery({
    queryKey: ["report", id, "results", locale, paging, scope ?? null],
    queryFn: async () =>
      (
        await reportResults({
          path: { report_id: id },
          query: {
            page: paging.page,
            pageSize: paging.pageSize,
            sort: paging.sort,
            s: scope,
          },
          throwOnError: true,
        })
      ).data,
    placeholderData: keepPreviousData,
    refetchOnWindowFocus: true,
  })
}

export function useSaveReport(id?: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: ReportInput) =>
      id === undefined
        ? (await createReport({ body, throwOnError: true })).data
        : (
            await updateReport({
              path: { report_id: id },
              body,
              throwOnError: true,
            })
          ).data,
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["reports"] })
      await queryClient.invalidateQueries({ queryKey: ["report", saved.id] })
    },
  })
}

export function useDeleteReport() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: number) => {
      await deleteReport({ path: { report_id: id }, throwOnError: true })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["reports"] })
    },
  })
}
