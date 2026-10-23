import * as path from 'path'
import * as cdk from 'aws-cdk-lib'
import * as budgets from 'aws-cdk-lib/aws-budgets'
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch'
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions'
import * as iam from 'aws-cdk-lib/aws-iam'
import * as lambda from 'aws-cdk-lib/aws-lambda'
import * as logs from 'aws-cdk-lib/aws-logs'
import * as sns from 'aws-cdk-lib/aws-sns'
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions'
import { Construct } from 'constructs'
import { INFRA_DIR, SIMULATOR_STARTED_BY, type ArgusConfig } from './config'
import type { AppStack } from './app-stack'
import type { SimulatorStack } from './simulator-stack'

export interface GuardrailsStackProps extends cdk.StackProps {
  app: AppStack
  simulator: SimulatorStack
  config: ArgusConfig
}

/** Backend requests per hour a demo makes at the final-tweaks cadence
 * (docs/designs/final-tweaks-contracts.md, C4). Every device write is one
 * `POST /api/devices/me/report` every 5 s (`REPORT_INTERVAL_MS`; an event goes at once, but it
 * replaces the next report rather than adding one, and the 60 s heartbeat is gone); live retries
 * are at most one a second for 60 s per incident, which a demo hour barely notices. The dashboard
 * polls `/api/live` and alert history every 5 s and the simulator panel every 10 s, and pauses
 * the live poll while its tab is hidden. */
export const DEMO_LOAD_PER_HOUR = {
  /** One real truck (ESP32) reporting all hour. */
  realTruck: 3600 / 5,
  /** One 10-minute simulator session of 6 trucks (each posts the same report). */
  simulatorSession: 6 * ((10 * 60) / 5),
  /** One open dashboard tab: /api/live 5 s + alert history 5 s + simulator status 10 s. */
  dashboardTab: 3600 / 5 + 3600 / 5 + 3600 / 10,
}

/** Invocations per hour that trip the kill switch. A busy demo hour (one real truck, a simulator
 * session and three dashboard tabs, see `DEMO_LOAD_PER_HOUR`) is ~6,800 requests. 20k/hour leaves
 * room for that three times over and is still far below anything that costs money. */
export const KILL_SWITCH_INVOCATIONS_PER_HOUR = 20000

/**
 * Account-level cost backstops that don't depend on the backend behaving:
 *   R6  monthly AWS Budget ($3): email at 50/80/100%, and at 100% an automatic budget action
 *       attaches a deny-ecs:RunTask policy to the backend's role (no more simulator starts until
 *       someone detaches it — see README).
 *   R8  an alarm on backend invocations/hour -> SNS -> kill-switch Lambda: backend concurrency
 *       set to 0 (every request throttled) and all simulator tasks stopped. `npm run unkill`
 *       restores it.
 * AWS Budgets: the first two budgets (and their actions) are free. Alarms/SNS: free tier.
 */
export class GuardrailsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: GuardrailsStackProps) {
    super(scope, id, props)
    const { app, simulator, config } = props
    const backendRole = app.backend.role!

    if (!config.alertEmail) {
      cdk.Annotations.of(this).addWarningV2(
        'argus:no-alert-email',
        'No alertEmail context set: budget/kill-switch alerts will not email anyone. Deploy with -c alertEmail=you@example.com',
      )
    }
    const subscribers = config.alertEmail
      ? [{ subscriptionType: 'EMAIL', address: config.alertEmail }]
      : []

    // --- R6: budget + budget action ------------------------------------------------------
    const budgetName = 'argus-demo-monthly'
    new budgets.CfnBudget(this, 'Budget', {
      budget: {
        budgetName,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: config.budgetUsd, unit: 'USD' },
      },
      notificationsWithSubscribers: subscribers.length
        ? [50, 80, 100].map((threshold) => ({
            notification: {
              notificationType: 'ACTUAL',
              comparisonOperator: 'GREATER_THAN',
              threshold,
              thresholdType: 'PERCENTAGE',
            },
            subscribers,
          }))
        : undefined,
    })

    const denyRunTask = new iam.ManagedPolicy(this, 'DenySimulatorStart', {
      description: 'Attached by the Argus budget action at 100% of budget: no more simulator starts',
      statements: [new iam.PolicyStatement({ effect: iam.Effect.DENY, actions: ['ecs:RunTask'], resources: ['*'] })],
    })

    const budgetActionRole = new iam.Role(this, 'BudgetActionRole', {
      assumedBy: new iam.ServicePrincipal('budgets.amazonaws.com'),
    })
    budgetActionRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['iam:AttachRolePolicy', 'iam:DetachRolePolicy'],
        resources: [backendRole.roleArn],
        conditions: { ArnEquals: { 'iam:PolicyARN': denyRunTask.managedPolicyArn } },
      }),
    )

    if (subscribers.length) {
      // CloudFormation requires at least one subscriber on a budget action.
      const action = new budgets.CfnBudgetsAction(this, 'BudgetAction', {
        budgetName,
        actionType: 'APPLY_IAM_POLICY',
        approvalModel: 'AUTOMATIC',
        notificationType: 'ACTUAL',
        actionThreshold: { type: 'PERCENTAGE', value: 100 },
        executionRoleArn: budgetActionRole.roleArn,
        definition: {
          iamActionDefinition: { policyArn: denyRunTask.managedPolicyArn, roles: [backendRole.roleName] },
        },
        subscribers: [{ type: 'EMAIL', address: config.alertEmail }],
      })
      action.node.addDependency(this.node.findChild('Budget'))
    }

    // --- R8: invocation alarm -> kill switch ---------------------------------------------
    const topic = new sns.Topic(this, 'KillSwitchTopic')
    if (config.alertEmail) topic.addSubscription(new subs.EmailSubscription(config.alertEmail))

    const killLogs = new logs.LogGroup(this, 'KillSwitchLogs', {
      retention: logs.RetentionDays.THREE_DAYS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })
    const killSwitch = new lambda.Function(this, 'KillSwitch', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'kill_switch.handler',
      code: lambda.Code.fromAsset(path.join(INFRA_DIR, 'lambda'), { exclude: ['*', '!kill_switch.py'] }),
      memorySize: 128,
      timeout: cdk.Duration.seconds(30),
      logGroup: killLogs,
      environment: {
        BACKEND_FUNCTION: app.backend.functionName,
        CLUSTER: simulator.cluster.clusterName,
        STARTED_BY: SIMULATOR_STARTED_BY,
      },
    })
    killSwitch.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['lambda:PutFunctionConcurrency'], resources: [app.backend.functionArn] }),
    )
    killSwitch.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ecs:ListTasks'],
        resources: ['*'],
        conditions: { ArnEquals: { 'ecs:cluster': simulator.cluster.clusterArn } },
      }),
    )
    killSwitch.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['ecs:StopTask'], resources: [simulator.taskArnPattern()] }),
    )
    topic.addSubscription(new subs.LambdaSubscription(killSwitch))

    const alarm = new cloudwatch.Alarm(this, 'BackendInvocationSpike', {
      alarmDescription: `More than ${KILL_SWITCH_INVOCATIONS_PER_HOUR} backend invocations in an hour: kill switch`,
      metric: app.backend.metricInvocations({ period: cdk.Duration.hours(1), statistic: 'Sum' }),
      threshold: KILL_SWITCH_INVOCATIONS_PER_HOUR,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    })
    alarm.addAlarmAction(new cwActions.SnsAction(topic))
  }
}
