import { defineConfig, devices } from '@playwright/test'

/**
 * `baseURL` defaults to `http://localhost:5173` and that default is correct in BOTH cases this
 * config runs under — running `npx playwright test` locally against an already-running dev
 * stack, and running via this module's own `docker-compose.yml`, which deliberately shares
 * `ui-argus`'s network namespace (see that file's `network_mode` comment) specifically so
 * `localhost:5173` resolves there too. That sharing exists for a real reason, not just
 * convenience: `crypto.subtle` (used by the real login flow) requires a browser "secure
 * context" — HTTPS, or the host `localhost` — and the internal Docker DNS name
 * (`http://ui-argus:5173`) is neither, which silently broke login the first time this was
 * tried. `IT_BASE_URL` still exists as an override for a differently-shaped setup.
 */
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  // `list` for live console output, `html` for a browsable report with per-test screenshots/
  // traces after the fact — `open: 'never'` so a local `npx playwright test` doesn't try to pop
  // a browser tab (there's no display in the Docker Compose run at all). See docker-compose.yml
  // for the volume mount that gets this report out of the `it-argus` container and onto the
  // host; without it, the report would just vanish when the container is torn down.
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  // The default 5000ms was tuned against auth.spec.ts's original 4 specs (one request each).
  // users.spec.ts/trucks.spec.ts/drivers.spec.ts do more real backend work per assertion
  // (several sequential logins/creates, each involving a CPU-bound bcrypt hash against a
  // single-process Uvicorn backend — see backend-argus's CLAUDE.md's "Auth design"), and 6
  // Playwright workers hitting that one process concurrently occasionally pushed a single
  // request past 5s — a real, observed flake, not a hypothetical one.
  expect: { timeout: 10000 },
  use: {
    baseURL: process.env.IT_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
