/**
 * Where the stack serves the dashboard and API: compose builds the image with BASE_PATH
 * /aws-inventory (backend/Dockerfile). It ends in "/" so the tests' relative paths ("resources",
 * "api/v1/...") resolve under the prefix; a leading "/" would drop it.
 */
export const BASE_URL =
  process.env.E2E_BASE_URL ?? "http://localhost:8000/aws-inventory/"
export const BASE_PATH = new URL(BASE_URL).pathname.replace(/\/$/, "")
