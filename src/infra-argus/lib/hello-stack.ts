import * as cdk from 'aws-cdk-lib'
import * as lambda from 'aws-cdk-lib/aws-lambda'
import * as logs from 'aws-cdk-lib/aws-logs'
import { Construct } from 'constructs'

/**
 * A throwaway "hello world" to prove the AWS account, region, CDK bootstrap, and deploy flow
 * work before deploying the real Argus stacks. One tiny Lambda behind a public Function URL that
 * returns a JSON greeting (with the region it runs in). Costs $0 (Lambda free tier). Deployed
 * and destroyed on its own via bin/hello.ts — the real stacks (bin/argus.ts) never include it.
 */
export class HelloStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props)

    const fn = new lambda.Function(this, 'Hello', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromInline(
        [
          'import json, os',
          'def handler(event, context):',
          '    return {',
          '        "statusCode": 200,',
          '        "headers": {"content-type": "application/json"},',
          '        "body": json.dumps({"message": "Hello from Argus", "region": os.environ["AWS_REGION"]}),',
          '    }',
        ].join('\n'),
      ),
      memorySize: 128,
      timeout: cdk.Duration.seconds(5),
      logGroup: new logs.LogGroup(this, 'HelloLogs', {
        retention: logs.RetentionDays.ONE_DAY,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    const url = fn.addFunctionUrl({ authType: lambda.FunctionUrlAuthType.NONE })
    new cdk.CfnOutput(this, 'HelloUrl', { value: url.url })
  }
}
