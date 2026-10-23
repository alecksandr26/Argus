import * as fs from 'fs'
import * as path from 'path'
import * as cdk from 'aws-cdk-lib'
import { Match, Template } from 'aws-cdk-lib/assertions'
import { buildArgus } from '../lib/argus'
import { DEMO_LOAD_PER_HOUR, KILL_SWITCH_INVOCATIONS_PER_HOUR } from '../lib/guardrails-stack'
import { SIMULATOR_TASK_SETTINGS } from '../lib/simulator-stack'

/**
 * The hard cost requirements (README "Cost guardrails", R1–R10) as synth-time assertions: if a
 * change ever adds an always-on paid resource or loosens a cap, `npm test` fails.
 */

const env = { account: '123456789012', region: 'us-east-1' }

function synth(context: Record<string, unknown> = {}) {
  const app = new cdk.App({ context: { alertEmail: 'demo@example.com', ...context } })
  const stacks = buildArgus(app, env)
  return {
    network: Template.fromStack(stacks.network),
    simulator: Template.fromStack(stacks.simulator),
    app: Template.fromStack(stacks.app),
    guardrails: Template.fromStack(stacks.guardrails),
    all: [stacks.network, stacks.simulator, stacks.app, stacks.guardrails].map((s) => Template.fromStack(s)),
  }
}

const t = synth()

describe('R7: no always-on paid resources anywhere', () => {
  const forbidden = [
    'AWS::EC2::NatGateway',
    'AWS::EC2::Instance',
    'AWS::AutoScaling::AutoScalingGroup',
    'AWS::ElasticLoadBalancingV2::LoadBalancer',
    'AWS::ElasticLoadBalancing::LoadBalancer',
    'AWS::RDS::DBInstance',
    'AWS::RDS::DBCluster',
    'AWS::DocDB::DBCluster',
    'AWS::DocDBElastic::Cluster',
    'AWS::SecretsManager::Secret',
    'AWS::WAFv2::WebACL',
    'AWS::ApiGateway::RestApi',
    'AWS::ApiGatewayV2::Api',
    'AWS::ECS::Service',
    'AWS::EFS::FileSystem',
    'AWS::ElastiCache::CacheCluster',
  ]
  it.each(forbidden)('%s count is 0', (type) => {
    for (const tpl of t.all) tpl.resourceCountIs(type, 0)
  })

  it('the VPC has public subnets only', () => {
    t.network.resourcePropertiesCountIs('AWS::EC2::Subnet', { MapPublicIpOnLaunch: false }, 0)
  })
})

describe('R1/R4: fixed 10-minute sessions on Fargate Spot', () => {
  it('cluster offers FARGATE_SPOT', () => {
    t.simulator.hasResourceProperties('AWS::ECS::ClusterCapacityProviderAssociations', {
      CapacityProviders: Match.arrayWith(['FARGATE_SPOT']),
    })
  })

  it('task is the small size', () => {
    t.simulator.hasResourceProperties('AWS::ECS::TaskDefinition', { Cpu: '1024', Memory: '2048' })
  })

  it('simulator container runs under a timeout clamped to 10 minutes', () => {
    t.simulator.hasResourceProperties('AWS::ECS::TaskDefinition', {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Name: 'simulator',
          EntryPoint: ['sh', '-c'],
          Command: [Match.stringLikeRegexp('\\[ "\\$m" -gt 10 \\] && m=10; exec timeout "\\$m"m python -m simulator_argus')],
          Environment: Match.arrayWith([{ Name: 'SIMULATOR_SESSION_MINUTES', Value: '10' }]),
        }),
      ]),
    })
  })

  it('simulator task gets its settings explicitly, reporting every 5 s (the demo-load cadence)', () => {
    const tasks = t.simulator.findResources('AWS::ECS::TaskDefinition')
    const containers = Object.values(tasks).flatMap((r) => r.Properties.ContainerDefinitions)
    const sim = containers.find((c: { Name: string }) => c.Name === 'simulator')
    const envMap = Object.fromEntries(
      (sim.Environment as { Name: string; Value: string }[]).map((e) => [e.Name, e.Value]),
    )
    expect(envMap).toMatchObject({
      SIMULATOR_REPORT_INTERVAL_S: '5',
      SIMULATOR_NORMAL_BLIP_CHANCE: '0.5',
      SIMULATOR_OUTAGE_CHANCE: '0',
      SIMULATOR_GPS_LOSS_CHANCE: '0',
      SIMULATOR_OUTAGE_AROUND_CRITICAL: 'false',
      SIMULATOR_SCENARIO_WEIGHTS: 'normal=0.45,drowsy_escalation=0.35,no_face=0.20',
    })
    // DEMO_LOAD_PER_HOUR.simulatorSession is 6 trucks x one report per 5 s for 10 minutes.
    const intervalS = Number(SIMULATOR_TASK_SETTINGS.SIMULATOR_REPORT_INTERVAL_S)
    expect(DEMO_LOAD_PER_HOUR.simulatorSession).toBe(6 * ((10 * 60) / intervalS))
  })

  it('a missing OSRM graph falls back to straight lines instead of killing the task', () => {
    t.simulator.hasResourceProperties('AWS::ECS::TaskDefinition', {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Name: 'osrm',
          Command: [Match.stringLikeRegexp('touch /tmp/osrm-off; exec sleep infinity')],
          HealthCheck: Match.objectLike({
            Command: ['CMD-SHELL', Match.stringLikeRegexp('^\\[ -f /tmp/osrm-off \\] \\|\\|')],
          }),
          DependsOn: [{ ContainerName: 'osrm-data', Condition: 'COMPLETE' }],
        }),
      ]),
    })
  })

  it('backend is told the session length and quota', () => {
    t.app.hasResourceProperties('AWS::Lambda::Function', {
      Environment: {
        Variables: Match.objectLike({
          SIMULATOR_CONTROL: 'ecs',
          SIMULATOR_SESSION_MINUTES: '10',
          SIMULATOR_MONTHLY_QUOTA_MINUTES: '120',
        }),
      },
    })
  })

  it('a session longer than 10 minutes cannot even be synthesized', () => {
    expect(() => synth({ simulatorSessionMinutes: 30 })).toThrow(/1-10/)
  })
})

describe('R5: reaper every minute, cutoff = one session', () => {
  it('runs every minute', () => {
    t.simulator.hasResourceProperties('AWS::Events::Rule', { ScheduleExpression: 'rate(1 minute)' })
  })
  it('stops tasks older than 10 minutes', () => {
    t.simulator.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'reaper.handler',
      Environment: { Variables: Match.objectLike({ MAX_MINUTES: '10', STARTED_BY: 'argus-simulator' }) },
    })
  })
})

describe('R6: budget with an automatic deny-RunTask action', () => {
  it('budget is $3/month with 50/80/100% alerts', () => {
    t.guardrails.hasResourceProperties('AWS::Budgets::Budget', {
      Budget: Match.objectLike({ BudgetLimit: { Amount: 3, Unit: 'USD' }, TimeUnit: 'MONTHLY' }),
      NotificationsWithSubscribers: Match.arrayWith([
        Match.objectLike({ Notification: Match.objectLike({ Threshold: 100 }) }),
      ]),
    })
  })
  it('budget action denies ecs:RunTask automatically at 100%', () => {
    t.guardrails.hasResourceProperties('AWS::Budgets::BudgetsAction', {
      ActionType: 'APPLY_IAM_POLICY',
      ApprovalModel: 'AUTOMATIC',
      ActionThreshold: { Type: 'PERCENTAGE', Value: 100 },
    })
    t.guardrails.hasResourceProperties('AWS::IAM::ManagedPolicy', {
      PolicyDocument: {
        Statement: [Match.objectLike({ Effect: 'Deny', Action: 'ecs:RunTask' })],
      },
    })
  })
})

describe('R8: small backend + kill switch', () => {
  it('backend Lambda is 512 MB / 10 s / concurrency 2', () => {
    t.app.hasResourceProperties('AWS::Lambda::Function', {
      PackageType: 'Image',
      MemorySize: 512,
      Timeout: 10,
      ReservedConcurrentExecutions: 2,
    })
  })
  it('concurrency reservation can be turned off for low-limit accounts', () => {
    const tpl = synth({ backendReservedConcurrency: 'none' }).app
    tpl.hasResourceProperties('AWS::Lambda::Function', {
      PackageType: 'Image',
      ReservedConcurrentExecutions: Match.absent(),
    })
  })
  it('a new backend image id is a new Lambda image (a backend change always redeploys)', () => {
    const imageUri = (id: string) => {
      const fns = synth({ backendImageId: id }).app.findResources('AWS::Lambda::Function', {
        Properties: { PackageType: 'Image' },
      })
      return JSON.stringify(Object.values(fns)[0].Properties.Code.ImageUri)
    }
    expect(imageUri('sha256:aaa')).not.toEqual(imageUri('sha256:bbb'))
    expect(imageUri('sha256:aaa')).toEqual(imageUri('sha256:aaa'))
  })
  it('invocation alarm triggers the kill switch', () => {
    t.guardrails.hasResourceProperties('AWS::CloudWatch::Alarm', {
      Threshold: KILL_SWITCH_INVOCATIONS_PER_HOUR,
      Period: 3600,
      AlarmActions: Match.anyValue(),
    })
    t.guardrails.hasResourceProperties('AWS::SNS::Subscription', { Protocol: 'lambda' })
  })
  it('backend Lambda timeout fits the backend route lock (wait < timeout <= lease)', () => {
    // src/backend-argus/app/constants.py: a request waits ROUTE_LOCK_WAIT for a route's lock,
    // then answers 503; a holder killed by the Lambda timeout frees the lock after
    // ROUTE_LOCK_LEASE. Read from the backend source so the two modules can't drift apart.
    const constants = fs.readFileSync(
      path.resolve(__dirname, '..', '..', 'backend-argus', 'app', 'constants.py'),
      'utf8',
    )
    const seconds = (name: string) => {
      const pattern = new RegExp(`^${name}\\s*=\\s*timedelta\\(seconds=(\\d+(?:\\.\\d+)?)\\)`, 'm')
      const m = constants.match(pattern)
      if (!m) throw new Error(`${name} = timedelta(seconds=...) not found in backend constants.py`)
      return Number(m[1])
    }
    const fns = t.app.findResources('AWS::Lambda::Function', { Properties: { PackageType: 'Image' } })
    const timeouts = Object.values(fns).map((fn) => fn.Properties.Timeout as number)
    expect(timeouts).toHaveLength(1)
    const [timeout] = timeouts
    expect(timeout).toBeGreaterThan(seconds('ROUTE_LOCK_WAIT'))
    expect(timeout).toBeLessThanOrEqual(seconds('ROUTE_LOCK_LEASE'))
  })
  it('a busy demo hour at the 5 s report/poll cadence stays far below the kill switch', () => {
    // One real truck + one simulator session + three dashboard tabs, with 2x headroom: a normal
    // demo must never trip the kill switch, only a runaway loop.
    const busyHour =
      DEMO_LOAD_PER_HOUR.realTruck +
      DEMO_LOAD_PER_HOUR.simulatorSession +
      3 * DEMO_LOAD_PER_HOUR.dashboardTab
    expect(busyHour).toBe(6840)
    expect(2 * busyHour).toBeLessThan(KILL_SWITCH_INVOCATIONS_PER_HOUR)
    // The UI allows up to 10 simulator trucks (backend schemas/simulator.py): still 2x headroom.
    const maxTrucksHour = busyHour + (DEMO_LOAD_PER_HOUR.simulatorSession / 6) * (10 - 6)
    expect(2 * maxTrucksHour).toBeLessThan(KILL_SWITCH_INVOCATIONS_PER_HOUR)
  })
})

describe('R9: logs kept 3 days', () => {
  it('every log group has 3-day retention', () => {
    for (const tpl of t.all) {
      const groups = tpl.findResources('AWS::Logs::LogGroup')
      for (const [id, group] of Object.entries(groups)) {
        expect([id, (group as { Properties: { RetentionInDays?: number } }).Properties.RetentionInDays]).toEqual([id, 3])
      }
    }
  })
})

describe('app wiring', () => {
  it('CloudFront sends /api/* to the backend uncached, everything else to S3', () => {
    t.app.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        PriceClass: 'PriceClass_100',
        CacheBehaviors: Match.arrayWith([
          Match.objectLike({ PathPattern: '/api/*', ViewerProtocolPolicy: 'https-only' }),
        ]),
      }),
    })
  })
  it('no distribution-wide error rewrite (it would turn API 404s into 200s)', () => {
    t.app.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({ CustomErrorResponses: Match.absent() }),
    })
  })
  it('public Function URL has both invoke permissions (AWS requires both since late 2025)', () => {
    t.app.hasResourceProperties('AWS::Lambda::Permission', {
      Action: 'lambda:InvokeFunctionUrl',
      Principal: '*',
      FunctionUrlAuthType: 'NONE',
    })
    t.app.hasResourceProperties('AWS::Lambda::Permission', {
      Action: 'lambda:InvokeFunction',
      Principal: '*',
      InvokedViaFunctionUrl: true,
    })
  })
  it('backend secrets are SSM paths, never values', () => {
    t.app.hasResourceProperties('AWS::Lambda::Function', {
      Environment: {
        Variables: Match.objectLike({
          ARGUS_SSM_ENV: 'MONGO_URI=/argus/mongo-uri,JWT_SECRET=/argus/jwt-secret,ROOT_ADMIN_PASSWORD=/argus/root-admin-password',
        }),
      },
    })
  })
})
