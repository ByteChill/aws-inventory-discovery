/**
 * Placeholder for a data hook until the generated API client has the endpoint.
 * Pages read `{ data, isLoading, error, refetch }` like a TanStack Query result,
 * so wiring a real query later is a one-line swap.
 */
export const noData = {
  data: undefined,
  isLoading: false,
  error: undefined as unknown,
  refetch: () => {},
}
