import { defineConfig } from "@hey-api/openapi-ts"

// Reads the running API's schema by default; set OPENAPI_INPUT to a local file to
// generate offline.
export default defineConfig({
  input:
    process.env.OPENAPI_INPUT ?? "http://localhost:8000/api/v1/openapi.json",
  output: "src/client",
  plugins: ["@hey-api/client-fetch", "@hey-api/sdk"],
})
