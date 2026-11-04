/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin (`src/api/client.ts`); defaults to http://localhost:8000. */
  readonly VITE_API_BASE_URL?: string
  /** Git short SHA of this build, set by `src/infra-argus/scripts/deploy.sh` (contracts G6). */
  readonly VITE_GIT_SHA?: string
}
