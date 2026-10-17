import * as cdk from 'aws-cdk-lib'
import { AppStack } from './app-stack'
import { readConfig } from './config'
import { GuardrailsStack } from './guardrails-stack'
import { NetworkStack } from './network-stack'
import { SimulatorStack } from './simulator-stack'

/** All four stacks, in dependency order. Shared by bin/argus.ts and the tests. */
export function buildArgus(app: cdk.App, env?: cdk.Environment) {
  const config = readConfig(app)
  cdk.Tags.of(app).add('project', 'argus')

  const network = new NetworkStack(app, 'ArgusNetwork', { env })
  const simulator = new SimulatorStack(app, 'ArgusSimulator', { env, vpc: network.vpc, config })
  const site = new AppStack(app, 'ArgusApp', { env, vpc: network.vpc, simulator, config })
  const guardrails = new GuardrailsStack(app, 'ArgusGuardrails', { env, app: site, simulator, config })

  return { network, simulator, app: site, guardrails }
}
