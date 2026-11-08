import { defineConfig } from "@hey-api/openapi-ts"

// Generated from the schema exported by backend/scripts/export_openapi.py. Set
// OPENAPI_INPUT to read a running API instead
// (e.g. http://localhost:8000/api/v1/openapi.json).
export default defineConfig({
  input: process.env.OPENAPI_INPUT ?? "openapi.json",
  output: "src/client",
  plugins: [
    // The paths already start with /api/v1; requests stay same-origin.
    { name: "@hey-api/client-fetch", baseUrl: false },
    "@hey-api/sdk",
  ],
})
