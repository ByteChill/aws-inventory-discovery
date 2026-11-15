import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type {
  AccountBatchUpdate,
  AccountCreate,
  AccountScanPreviewRequest,
  AccountUpdate,
} from "@/client"
import {
  batchPolicy,
  createAccount,
  deleteAccount,
  importFromRegistry,
  regionOptions,
  restoreAccount,
  scanPreview,
  updateAccount,
} from "@/client"

function useInvalidateAccounts() {
  const queryClient = useQueryClient()
  return () => {
    for (const key of [
      "accounts",
      "account",
      "applications",
      "facets",
      "overview",
    ]) {
      queryClient.invalidateQueries({ queryKey: [key] })
    }
  }
}

export function useRegionOptions() {
  return useQuery({
    queryKey: ["accounts", "region-options"],
    queryFn: async () => (await regionOptions({ throwOnError: true })).data,
    staleTime: 5 * 60_000,
  })
}

export function useCreateAccount() {
  const invalidate = useInvalidateAccounts()
  return useMutation({
    mutationFn: async (body: AccountCreate) =>
      (await createAccount({ body, throwOnError: true })).data,
    onSuccess: invalidate,
  })
}

export function useUpdateAccount() {
  const invalidate = useInvalidateAccounts()
  return useMutation({
    mutationFn: async ({
      accountId,
      body,
    }: {
      accountId: string
      body: AccountUpdate
    }) =>
      (
        await updateAccount({
          path: { account_id: accountId },
          body,
          throwOnError: true,
        })
      ).data,
    onSuccess: invalidate,
  })
}

export function useDeleteAccount() {
  const invalidate = useInvalidateAccounts()
  return useMutation({
    mutationFn: async (accountId: string) =>
      (
        await deleteAccount({
          path: { account_id: accountId },
          throwOnError: true,
        })
      ).data,
    onSuccess: invalidate,
  })
}

export function useRestoreAccount() {
  const invalidate = useInvalidateAccounts()
  return useMutation({
    mutationFn: async (accountId: string) =>
      (
        await restoreAccount({
          path: { account_id: accountId },
          throwOnError: true,
        })
      ).data,
    onSuccess: invalidate,
  })
}

export function useBatchPolicy() {
  const invalidate = useInvalidateAccounts()
  return useMutation({
    mutationFn: async (body: AccountBatchUpdate) =>
      (await batchPolicy({ body, throwOnError: true })).data,
    onSuccess: invalidate,
  })
}

export function useImportAccounts() {
  const invalidate = useInvalidateAccounts()
  return useMutation({
    mutationFn: async () =>
      (await importFromRegistry({ throwOnError: true })).data,
    onSuccess: invalidate,
  })
}

export function usePreviewScanPolicy() {
  return useMutation({
    mutationFn: async (body: AccountScanPreviewRequest) =>
      (await scanPreview({ body, throwOnError: true })).data,
  })
}
