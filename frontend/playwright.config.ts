import { defineConfig, devices } from "@playwright/test"
import { BASE_URL } from "./e2e/base-url"

// The tests run against a running stack (README: 本地运行): FastAPI serves the built dashboard and /api.
export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  // One shared database: keep the flows in order, one at a time.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // In CI: the HTML report is uploaded as an artifact; the JUnit file feeds the run's summary page.
  reporter: process.env.CI
    ? [
        ["list"],
        ["html", { open: "never" }],
        ["junit", { outputFile: "test-results/junit.xml" }],
      ]
    : "list",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    locale: "zh-CN",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
})
