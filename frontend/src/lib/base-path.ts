/**
 * The path prefix the dashboard is served under ("/aws-inventory" behind the shared ALB, "" at the
 * root). Vite bakes it in from BASE_PATH at build time (vite.config.ts); the router and the API
 * client both start from it.
 */
export const basePath = import.meta.env.BASE_URL.replace(/\/$/, "")
