import { defineConfig } from 'vite'
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
})
