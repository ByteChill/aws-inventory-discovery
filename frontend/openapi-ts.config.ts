import { defineConfig } from "@hey-api/openapi-ts"

// Generated from the schema exported by backend/scripts/export_openapi.py, which leaves out the
// deployment prefix. Not from a running API: its schema's paths carry BASE_PATH, and the client
// adds that prefix itself (baseUrl, src/api/client.ts).
export default defineConfig({
  input: "openapi.json",
  output: "src/client",
  plugins: [
    // The paths start with /api/v1; the prefix is set at run time.
    { name: "@hey-api/client-fetch", baseUrl: false },
    "@hey-api/sdk",
  ],
})
