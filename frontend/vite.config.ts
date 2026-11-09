import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react-swc"
import { defineConfig, loadEnv } from "vite"

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // The path prefix the dashboard and API are served under. The image build passes it in
  // (backend/Dockerfile, ARG BASE_PATH); the default is for `npm run dev` against compose.
  const env = loadEnv(mode, import.meta.dirname, "")
  const basePath = (env.BASE_PATH ?? "/aws-inventory").replace(/\/$/, "")
  return {
    base: `${basePath}/`,
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "./src"),
      },
    },
    server: {
      proxy: {
        // Matched before Vite strips `base`, so the key carries the prefix; the API expects it too.
        [`${basePath}/api`]: env.API_URL ?? "http://localhost:8000",
      },
    },
    plugins: [
      tanstackRouter({
        target: "react",
        autoCodeSplitting: true,
      }),
      react(),
      tailwindcss(),
    ],
  }
})
