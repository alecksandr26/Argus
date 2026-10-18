import * as cdk from 'aws-cdk-lib'
import { Match, Template } from 'aws-cdk-lib/assertions'
import { buildArgus } from '../lib/argus'
import { KILL_SWITCH_INVOCATIONS_PER_HOUR } from '../lib/guardrails-stack'

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
  it('invocation alarm triggers the kill switch', () => {
    t.guardrails.hasResourceProperties('AWS::CloudWatch::Alarm', {
      Threshold: KILL_SWITCH_INVOCATIONS_PER_HOUR,
      Period: 3600,
      AlarmActions: Match.anyValue(),
    })
    t.guardrails.hasResourceProperties('AWS::SNS::Subscription', { Protocol: 'lambda' })
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
