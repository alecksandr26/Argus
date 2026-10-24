import * as path from 'path'
import * as cdk from 'aws-cdk-lib'
import * as ec2 from 'aws-cdk-lib/aws-ec2'
import * as ecs from 'aws-cdk-lib/aws-ecs'
import * as events from 'aws-cdk-lib/aws-events'
import * as targets from 'aws-cdk-lib/aws-events-targets'
import * as iam from 'aws-cdk-lib/aws-iam'
import * as lambda from 'aws-cdk-lib/aws-lambda'
import * as logs from 'aws-cdk-lib/aws-logs'
import * as s3 from 'aws-cdk-lib/aws-s3'
import * as ssm from 'aws-cdk-lib/aws-ssm'
import { Platform } from 'aws-cdk-lib/aws-ecr-assets'
import { Construct } from 'constructs'
import { INFRA_DIR, SIMULATOR_STARTED_BY, SRC_DIR, SSM, type ArgusConfig } from './config'

const OSRM_GRAPH = '/data/mexico-latest.osrm'
/** Written by the osrm container when it runs without a graph, so its health check passes. */
const OSRM_OFF_MARKER = '/tmp/osrm-off'

/**
 * The simulator's behaviour settings, passed explicitly so a deployed session doesn't depend on
 * whatever `src/simulator-argus/simulator_argus/config.py` defaults to at build time. Values match
 * that module's `.env.example` / root `docker-compose.yml`. The backend's `run_task` override adds
 * the per-session ones (`SIMULATOR_SESSION_MINUTES`, `SIMULATOR_TRUCK_COUNT`,
 * `SIMULATOR_ROUTE_DURATION_MINUTES`, `BACKEND_BASE_URL`).
 *
 * `SIMULATOR_REPORT_INTERVAL_S` is a cost input: `DEMO_LOAD_PER_HOUR.simulatorSession`
 * (guardrails-stack.ts) assumes one report per truck every 5 s, and `npm test` pins it here.
 * Lowering it multiplies backend invocations; re-check the kill-switch margin if you do.
 */
export const SIMULATOR_TASK_SETTINGS = {
  /** One points-only report per truck every 5 s (contracts C4, mirrors the ESP32's REPORT_INTERVAL_MS). */
  SIMULATOR_REPORT_INTERVAL_S: '5',
  SIMULATOR_SCENARIO_WEIGHTS: 'normal=0.45,drowsy_escalation=0.35,no_face=0.20',
  /** Chance a `normal` route gets its one head_down medium blip. */
  SIMULATOR_NORMAL_BLIP_CHANCE: '0.5',
  /** Demo disruptions (outage = failed reports + dead-letter replay, GPS loss = last-known
   * positions), off as in the simulator's own defaults. Outages send nothing, so turning them on
   * only lowers the request count. */
  SIMULATOR_OUTAGE_CHANCE: '0',
  SIMULATOR_GPS_LOSS_CHANCE: '0',
  SIMULATOR_OUTAGE_AROUND_CRITICAL: 'false',
} as const

export interface SimulatorStackProps extends cdk.StackProps {
  vpc: ec2.IVpc
  config: ArgusConfig
}

/**
 * The on-demand simulator: one ECS Fargate **Spot** task per 10-minute session, started and
 * stopped by backend-argus (`/api/simulator`, root_admin only). Nothing here runs or bills
 * while the simulator is off except S3 storage for the OSRM data.
 *
 * Task layout (all three share one task, so `localhost` reaches OSRM):
 *   osrm-data  aws-cli: `s3 sync` the preprocessed Mexico graph onto the task's disk, then exit
 *   osrm       osrm-routed --mmap over that data (after osrm-data succeeds)
 *   simulator  the unchanged src/simulator-argus image, under `timeout` (after osrm is healthy)
 *
 * Cost guardrails here: R4 (in-container `timeout`, capped at 10 min whatever the override
 * says), R5 (the reaper, every minute), and Spot + the smallest size OSRM can run in.
 */
export class SimulatorStack extends cdk.Stack {
  readonly cluster: ecs.Cluster
  readonly taskDefinition: ecs.FargateTaskDefinition
  readonly securityGroup: ec2.SecurityGroup
  readonly osrmBucket: s3.Bucket

  constructor(scope: Construct, id: string, props: SimulatorStackProps) {
    super(scope, id, props)
    const { vpc, config } = props
    const sessionMinutes = config.simulatorSessionMinutes

    this.cluster = new ecs.Cluster(this, 'Cluster', {
      vpc,
      // Registers FARGATE + FARGATE_SPOT; the backend's run_task asks for FARGATE_SPOT only.
      enableFargateCapacityProviders: true,
      containerInsightsV2: ecs.ContainerInsights.DISABLED,
    })

    this.securityGroup = new ec2.SecurityGroup(this, 'TaskSg', {
      vpc,
      description: 'Argus simulator task: egress only (backend API, S3, image pulls)',
      allowAllOutbound: true,
    })

    // Preprocessed OSRM files (~8.6 GB), uploaded once by scripts/osrm-upload.sh. S3 (~$0.20/month)
    // instead of baking them into a 9 GB image (~$0.95/month in ECR, and slower pulls).
    this.osrmBucket = new s3.Bucket(this, 'OsrmData', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    })

    const logGroup = new logs.LogGroup(this, 'TaskLogs', {
      retention: logs.RetentionDays.THREE_DAYS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })

    this.taskDefinition = new ecs.FargateTaskDefinition(this, 'Task', {
      family: 'argus-simulator',
      cpu: 1024,
      memoryLimitMiB: 2048,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
      // 20 GiB default ephemeral storage (free) fits the 8.6 GB graph + three images.
    })
    this.taskDefinition.addVolume({ name: 'osrm-data' })
    this.osrmBucket.grantRead(this.taskDefinition.taskRole)

    const osrmData = this.taskDefinition.addContainer('osrm-data', {
      image: ecs.ContainerImage.fromRegistry('public.ecr.aws/aws-cli/aws-cli:2.22.0'),
      essential: false,
      command: ['s3', 'sync', `s3://${this.osrmBucket.bucketName}/`, '/data/', '--only-show-errors'],
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'osrm-data', logGroup }),
    })
    osrmData.addMountPoints({ containerPath: '/data', sourceVolume: 'osrm-data', readOnly: false })

    const osrm = this.taskDefinition.addContainer('osrm', {
      // Must be the same image scripts/setup_osrm.sh preprocessed the data with
      // (src/simulator-argus/scripts/setup_osrm.sh) — OSRM's file format is version-specific.
      image: ecs.ContainerImage.fromRegistry('osrm/osrm-backend'),
      essential: true,
      // Missing graph (osrm:upload not run) or an osrm-routed crash must not kill the task: stay up
      // and report healthy, so the simulator still starts and falls back to straight-line routes.
      entryPoint: ['sh', '-c'],
      command: [
        `f=${OSRM_GRAPH}; ` +
          `if [ -f "$f.fileIndex" ]; then osrm-routed --algorithm mld --mmap "$f"; ` +
          `echo "osrm-routed exited ($?); simulator falls back to straight-line routes"; ` +
          `else echo "No OSRM data in S3 (npm run osrm:upload); simulator uses straight-line routes"; fi; ` +
          `touch ${OSRM_OFF_MARKER}; exec sleep infinity`,
      ],
      healthCheck: {
        command: ['CMD-SHELL', `[ -f ${OSRM_OFF_MARKER} ] || bash -c '</dev/tcp/127.0.0.1/5000' || exit 1`],
        interval: cdk.Duration.seconds(10),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(30),
      },
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'osrm', logGroup }),
    })
    osrm.addMountPoints({ containerPath: '/data', sourceVolume: 'osrm-data', readOnly: true })
    // COMPLETE, not SUCCESS: a failed sync just means no graph, which the osrm command handles.
    osrm.addContainerDependencies({ container: osrmData, condition: ecs.ContainerDependencyCondition.COMPLETE })

    const adminPassword = ssm.StringParameter.fromSecureStringParameterAttributes(this, 'AdminPassword', {
      parameterName: SSM.rootAdminPassword,
    })

    const simulator = this.taskDefinition.addContainer('simulator', {
      image: ecs.ContainerImage.fromAsset(path.join(SRC_DIR, 'simulator-argus'), {
        platform: Platform.LINUX_AMD64,
        // The 9 GB OSRM graph lives next to the simulator locally; never ship it as build context.
        exclude: ['osrm-data', '.pytest_cache', '**/__pycache__', '.env'],
      }),
      essential: true,
      // R4: the container ends itself. The minutes come from the backend's run-time override,
      // but are clamped to the session cap here too, so no override can lengthen a session.
      entryPoint: ['sh', '-c'],
      command: [
        `m="\${SIMULATOR_SESSION_MINUTES:-${sessionMinutes}}"; ` +
          `case "$m" in ''|*[!0-9]*) m=${sessionMinutes};; esac; ` +
          `[ "$m" -gt ${sessionMinutes} ] && m=${sessionMinutes}; ` +
          `exec timeout "$m"m python -m simulator_argus`,
      ],
      environment: {
        ...SIMULATOR_TASK_SETTINGS,
        SIMULATOR_SESSION_MINUTES: String(sessionMinutes),
        SIMULATOR_ROUTE_DURATION_MINUTES: String(sessionMinutes),
        SIMULATOR_ADMIN_EMAIL: config.rootAdminEmail,
        SIMULATOR_USE_OSRM: 'true',
        OSRM_BASE_URL: 'http://localhost:5000',
        SIMULATOR_RESET: 'true',
      },
      secrets: {
        SIMULATOR_ADMIN_PASSWORD: ecs.Secret.fromSsmParameter(adminPassword),
      },
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'simulator', logGroup }),
    })
    simulator.addContainerDependencies({ container: osrm, condition: ecs.ContainerDependencyCondition.HEALTHY })

    // R5: independent of the backend, stop any simulator task older than one session.
    const reaperLogs = new logs.LogGroup(this, 'ReaperLogs', {
      retention: logs.RetentionDays.THREE_DAYS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })
    const reaper = new lambda.Function(this, 'Reaper', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'reaper.handler',
      code: lambda.Code.fromAsset(path.join(INFRA_DIR, 'lambda'), { exclude: ['*', '!reaper.py'] }),
      memorySize: 128,
      timeout: cdk.Duration.seconds(30),
      logGroup: reaperLogs,
      environment: {
        CLUSTER: this.cluster.clusterName,
        STARTED_BY: SIMULATOR_STARTED_BY,
        MAX_MINUTES: String(sessionMinutes),
      },
    })
    reaper.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ecs:ListTasks'],
        resources: ['*'],
        conditions: { ArnEquals: { 'ecs:cluster': this.cluster.clusterArn } },
      }),
    )
    reaper.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ecs:DescribeTasks', 'ecs:StopTask'],
        resources: [this.taskArnPattern()],
      }),
    )
    new events.Rule(this, 'ReaperSchedule', {
      schedule: events.Schedule.rate(cdk.Duration.minutes(1)),
      targets: [new targets.LambdaFunction(reaper)],
    })

    new cdk.CfnOutput(this, 'OsrmBucketName', { value: this.osrmBucket.bucketName })
    new cdk.CfnOutput(this, 'ClusterName', { value: this.cluster.clusterName })
  }

  /** ARN pattern of this cluster's tasks, for StopTask/DescribeTasks permissions. */
  taskArnPattern(): string {
    return cdk.Stack.of(this).formatArn({
      service: 'ecs',
      resource: 'task',
      resourceName: `${this.cluster.clusterName}/*`,
    })
  }
}
