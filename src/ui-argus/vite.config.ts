// `defineConfig` from 'vitest/config', not plain 'vite' — a drop-in superset that also
// type-checks the `test` block below. Everything Vite-specific (plugins/server) is unchanged;
// this swap only widens the config's own type, it doesn't change dev/build behavior.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 0.0.0.0, not the default localhost-only bind: the dev server has to be reachable
    // from outside the container when run via `docker compose up` (see docker-compose.yml).
    host: true,
    port: 5173,
    strictPort: true,
    // Bind-mounted source on Docker Desktop (macOS/Windows) crosses a VM boundary that
    // doesn't always propagate inotify file-change events, so Vite's default watcher can
    // silently miss edits. Polling costs a little CPU but works everywhere, native Linux
    // Docker included, so it's kept on unconditionally rather than env-gated.
    watch: {
      usePolling: true,
    },
  },
  test: {
    // jsdom, not the default 'node' environment — every test here renders real React
    // components (React Testing Library), which needs a DOM to mount into.
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // No global `describe`/`it`/`expect` injection — every test file imports what it needs
    // from 'vitest' explicitly, so no tsconfig "types" edit is needed to make them typecheck.
    globals: false,
  },
})
