import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"

import "./index.css"
import { setupApiClient } from "./api/client"
import { I18nProvider } from "./i18n"
import { basePath } from "./lib/base-path"
import { initPreferences } from "./lib/preferences"
import { routeTree } from "./routeTree.gen"

initPreferences()
setupApiClient()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // An API answer (404, 400) will not change on retry; a dropped connection might.
      retry: (count, error) =>
        !(typeof error === "object" && error !== null && "code" in error) &&
        count < 2,
    },
  },
})
const router = createRouter({ routeTree, basepath: basePath || "/" })

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

// biome-ignore lint/style/noNonNullAssertion: index.html always has #root
ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </I18nProvider>
  </StrictMode>,
)
