import * as path from 'path'
import * as cdk from 'aws-cdk-lib'
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront'
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins'
import * as ec2 from 'aws-cdk-lib/aws-ec2'
import * as iam from 'aws-cdk-lib/aws-iam'
import * as lambda from 'aws-cdk-lib/aws-lambda'
import * as logs from 'aws-cdk-lib/aws-logs'
import * as s3 from 'aws-cdk-lib/aws-s3'
import { Platform } from 'aws-cdk-lib/aws-ecr-assets'
import { Construct } from 'constructs'
import { INFRA_DIR, SSM, type ArgusConfig } from './config'
import type { SimulatorStack } from './simulator-stack'

export interface AppStackProps extends cdk.StackProps {
  vpc: ec2.IVpc
  simulator: SimulatorStack
  config: ArgusConfig
}

/**
 * backend-argus on Lambda + ui-argus on S3, both behind ONE CloudFront distribution:
 *   /api/*, /health  -> the Lambda Function URL (no caching, all headers but Host forwarded)
 *   everything else  -> the private UI bucket (SPA paths rewritten to /index.html)
 * Same origin for the browser, so no CORS configuration and no build-order loop between the UI
 * bundle and the API URL. The UI files are uploaded by scripts/deploy.sh (`aws s3 sync`).
 *
 * The backend image is the UNCHANGED src/backend-argus image (built + tested first by
 * scripts/deploy.sh as argus/backend-argus:latest) plus the Lambda Web Adapter, see
 * docker/backend-lambda/Dockerfile. Pay-per-request only; nothing here bills while idle.
 */
export class AppStack extends cdk.Stack {
  readonly backend: lambda.DockerImageFunction
  readonly siteBucket: s3.Bucket
  readonly distribution: cloudfront.Distribution

  constructor(scope: Construct, id: string, props: AppStackProps) {
    super(scope, id, props)
    const { vpc, simulator, config } = props

    const backendLogs = new logs.LogGroup(this, 'BackendLogs', {
      retention: logs.RetentionDays.THREE_DAYS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })

    this.backend = new lambda.DockerImageFunction(this, 'Backend', {
      code: lambda.DockerImageCode.fromImageAsset(path.join(INFRA_DIR, 'docker', 'backend-lambda'), {
        platform: Platform.LINUX_AMD64,
        buildArgs: { BASE_IMAGE: 'argus/backend-argus:latest' },
      }),
      architecture: lambda.Architecture.X86_64,
      // R8: small, short, and (where the account allows it) capped concurrency.
      memorySize: 512,
      timeout: cdk.Duration.seconds(10),
      reservedConcurrentExecutions: config.backendReservedConcurrency,
      logGroup: backendLogs,
      environment: {
        // Secrets are fetched from SSM at container start by load_ssm_env.py (never stored in
        // the CloudFormation template). NAME=/ssm/path pairs.
        ARGUS_SSM_ENV: [
          `MONGO_URI=${SSM.mongoUri}`,
          `JWT_SECRET=${SSM.jwtSecret}`,
          `ROOT_ADMIN_PASSWORD=${SSM.rootAdminPassword}`,
        ].join(','),
        ROOT_ADMIN_EMAIL: config.rootAdminEmail,
        SEED_DEMO_DATA: 'false',
        SIMULATOR_CONTROL: 'ecs',
        SIMULATOR_ECS_CLUSTER: simulator.cluster.clusterName,
        SIMULATOR_TASK_DEFINITION: simulator.taskDefinition.family,
        SIMULATOR_SUBNETS: vpc.publicSubnets.map((s) => s.subnetId).join(','),
        SIMULATOR_SECURITY_GROUPS: simulator.securityGroup.securityGroupId,
        SIMULATOR_SESSION_MINUTES: String(config.simulatorSessionMinutes),
        SIMULATOR_MONTHLY_QUOTA_MINUTES: '120',
      },
    })

    this.backend.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameters'],
        resources: Object.values(SSM).map((name) =>
          this.formatArn({ service: 'ssm', resource: 'parameter', resourceName: name.replace(/^\//, '') }),
        ),
      }),
    )
    // Simulator control, scoped to the one cluster / task family.
    this.backend.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ecs:RunTask'],
        resources: [
          this.formatArn({
            service: 'ecs',
            resource: 'task-definition',
            resourceName: `${simulator.taskDefinition.family}:*`,
          }),
        ],
        conditions: { ArnEquals: { 'ecs:cluster': simulator.cluster.clusterArn } },
      }),
    )
    this.backend.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ecs:ListTasks'],
        resources: ['*'],
        conditions: { ArnEquals: { 'ecs:cluster': simulator.cluster.clusterArn } },
      }),
    )
    this.backend.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ecs:DescribeTasks', 'ecs:StopTask'],
        resources: [simulator.taskArnPattern()],
      }),
    )
    this.backend.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['iam:PassRole'],
        resources: [
          simulator.taskDefinition.taskRole.roleArn,
          simulator.taskDefinition.obtainExecutionRole().roleArn,
        ],
      }),
    )

    // The app does its own auth (JWT / device API key), so the URL itself is public.
    const fnUrl = this.backend.addFunctionUrl({ authType: lambda.FunctionUrlAuthType.NONE })

    this.siteBucket = new s3.Bucket(this, 'Site', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    })

    // SPA fallback as a viewer-request function on the UI behavior only. A distribution-wide
    // 403/404 -> /index.html error response would also rewrite the API's real 404s into 200s.
    const spaRewrite = new cloudfront.Function(this, 'SpaRewrite', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        [
          'function handler(event) {',
          '  var req = event.request;',
          "  if (req.uri.indexOf('.') === -1) { req.uri = '/index.html'; }",
          '  return req;',
          '}',
        ].join('\n'),
      ),
    })

    const apiBehavior: cloudfront.BehaviorOptions = {
      origin: new origins.FunctionUrlOrigin(fnUrl),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
    }

    this.distribution = new cloudfront.Distribution(this, 'SiteCdn', {
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        functionAssociations: [{ function: spaRewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      additionalBehaviors: {
        '/api/*': apiBehavior,
        '/health': apiBehavior,
      },
    })

    new cdk.CfnOutput(this, 'SiteUrl', { value: `https://${this.distribution.distributionDomainName}` })
    new cdk.CfnOutput(this, 'SiteBucketName', { value: this.siteBucket.bucketName })
    new cdk.CfnOutput(this, 'DistributionId', { value: this.distribution.distributionId })
    new cdk.CfnOutput(this, 'BackendFunctionUrl', { value: fnUrl.url })
    new cdk.CfnOutput(this, 'BackendFunctionName', { value: this.backend.functionName })
  }
}
