/**
 * Build versions (route guardian contracts G6). `src/infra-argus/scripts/deploy.sh` builds the UI
 * with `VITE_GIT_SHA` and the backend image with `ARGUS_VERSION`, both the same git short SHA, and
 * the backend reports its own on `GET /health`. A local build is 'dev' on both sides.
 */
export const UI_VERSION: string = import.meta.env.VITE_GIT_SHA || 'dev'

/** True only when both versions are known real builds (not 'dev') and differ. */
export function versionsDiffer(ui: string, backend: string | null | undefined): boolean {
  if (!backend || ui === 'dev' || backend === 'dev') return false
  return ui !== backend
}
