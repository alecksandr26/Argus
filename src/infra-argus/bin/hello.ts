#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib'
import { HelloStack } from '../lib/hello-stack'

// Separate CDK app so `npm run hello:deploy` / `hello:destroy` never touch the real stacks.
const app = new cdk.App()
cdk.Tags.of(app).add('project', 'argus')
new HelloStack(app, 'ArgusHello', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION },
})
