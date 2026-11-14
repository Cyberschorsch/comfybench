import {defineConfig, devices} from "@playwright/test";

// Smoke tests for the judge UI against a fixture data dir (e2e/fixture.py). Run: npm run test:e2e
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  use: {baseURL: "http://127.0.0.1:8799"},
  projects: [{name: "chromium", use: {...devices["Desktop Chrome"]}}],
  webServer: {
    command: "python3 e2e/fixture.py e2e/.home && COMFYBENCH_HOME=e2e/.home python3 bench.py serve --port 8799",
    url: "http://127.0.0.1:8799/api/run/status",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
