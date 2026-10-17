import * as cdk from 'aws-cdk-lib'
import * as ec2 from 'aws-cdk-lib/aws-ec2'
import { Construct } from 'constructs'

/**
 * Public subnets only, **zero NAT Gateways** (R7). CDK's default `Vpc` creates one NAT Gateway
 * per AZ (~$32/month each, billed 24/7) — the single biggest cost trap for this project. The
 * only thing in this VPC is the on-demand simulator task, which gets a public IP instead.
 * The backend Lambda is not in a VPC at all (it reaches Atlas over the internet).
 */
export class NetworkStack extends cdk.Stack {
  readonly vpc: ec2.Vpc

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props)

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [{ name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 }],
    })
  }
}
