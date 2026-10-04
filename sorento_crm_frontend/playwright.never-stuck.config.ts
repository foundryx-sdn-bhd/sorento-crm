import { defineConfig, devices } from '@playwright/test';

/**
 * Config for the never-stuck smoke only (`e2e/never-stuck.smoke.spec.ts`, guard G4). Kept
 * apart from `playwright.config.ts`: it needs a global sign-in step, runs parallel against
 * one server, and reports as JSON for the nightly job's summary.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: 'never-stuck.smoke.spec.ts',
  globalSetup: './e2e/never-stuck/global-setup.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // 2, measured: at 4 the backend saturates a 4-core runner (`/me/permissions` at 3-7 s)
  // and the dashboard's loaders flap past 15 s for reasons that are load, not product.
  workers: Number(process.env.NEVER_STUCK_WORKERS ?? 2),
  timeout: 45_000,
  reporter: [
    ['list'],
    ['json', { outputFile: 'test-results/never-stuck/results.json' }],
    ['html', { open: 'never', outputFolder: 'playwright-report/never-stuck' }],
  ],
  outputDir: 'test-results/never-stuck/artifacts',
  use: {
    baseURL: process.env.PORTAL_E2E_BASE_URL ?? 'http://localhost:3000',
    trace: 'off',
    screenshot: 'only-on-failure',
    video: 'off',
    // A sandbox whose preinstalled chromium does not match this Playwright version points here.
    launchOptions: process.env.NEVER_STUCK_CHROMIUM
      ? { executablePath: process.env.NEVER_STUCK_CHROMIUM }
      : {},
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } },
    },
  ],
});
