import * as path from 'path'
import { Construct } from 'constructs'

/** `src/` — every module's own directory hangs off this. */
export const SRC_DIR = path.resolve(__dirname, '..', '..')
export const INFRA_DIR = path.resolve(__dirname, '..')

/** Tag on every simulator task (ECS `startedBy`). Must match backend-argus's
 * `app/simulator_control.py` `STARTED_BY` — the reaper and kill switch find tasks by it. */
export const SIMULATOR_STARTED_BY = 'argus-simulator'

/** SSM SecureString parameters the user creates once before the first deploy (see README).
 * Standard-tier SSM params are free; Secrets Manager ($0.40/secret/month) is deliberately not used. */
export const SSM = {
  mongoUri: '/argus/mongo-uri',
  jwtSecret: '/argus/jwt-secret',
  rootAdminPassword: '/argus/root-admin-password',
} as const

export interface ArgusConfig {
  alertEmail: string
  rootAdminEmail: string
  budgetUsd: number
  /** `undefined` = no reservation (accounts whose Lambda concurrency limit is only 10 can't reserve any). */
  backendReservedConcurrency: number | undefined
  simulatorSessionMinutes: number
}

export function readConfig(scope: Construct): ArgusConfig {
  const ctx = (key: string) => scope.node.tryGetContext(key)
  const concurrency = ctx('backendReservedConcurrency')
  const sessionMinutes = Number(ctx('simulatorSessionMinutes') ?? 10)
  if (sessionMinutes < 1 || sessionMinutes > 10) {
    // R1 is a hard requirement — don't let a context override quietly lengthen sessions.
    throw new Error(`simulatorSessionMinutes must be 1-10, got ${sessionMinutes}`)
  }
  return {
    alertEmail: String(ctx('alertEmail') ?? ''),
    rootAdminEmail: String(ctx('rootAdminEmail') ?? 'admin@argus.dev'),
    budgetUsd: Number(ctx('budgetUsd') ?? 3),
    // Capped at 2 unless explicitly turned off with `-c backendReservedConcurrency=none`.
    backendReservedConcurrency: concurrency === 'none' ? undefined : Number(concurrency ?? 2),
    simulatorSessionMinutes: sessionMinutes,
  }
}
