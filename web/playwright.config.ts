import { defineConfig, devices } from "@playwright/test";

// The journeys run against the built app (vite preview) and a running API with stand-in
// model providers, so they are deterministic and free. CI starts both; see the release workflow.
export default defineConfig({
  testDir: "e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
});
