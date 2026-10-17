#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib'
import { buildArgus } from '../lib/argus'

const app = new cdk.App()
buildArgus(app, {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
})
