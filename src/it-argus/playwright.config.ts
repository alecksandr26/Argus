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
  reporter: [['list']],
  use: {
    baseURL: process.env.IT_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
