import * as cdk from 'aws-cdk-lib'
import { Match, Template } from 'aws-cdk-lib/assertions'
import { HelloStack } from '../lib/hello-stack'

describe('ArgusHello', () => {
  const t = Template.fromStack(new HelloStack(new cdk.App(), 'ArgusHello'))

  it('is one tiny Lambda with a public URL and nothing else that bills', () => {
    t.resourceCountIs('AWS::Lambda::Function', 1)
    t.hasResourceProperties('AWS::Lambda::Function', { MemorySize: 128, Runtime: 'python3.12' })
    t.hasResourceProperties('AWS::Lambda::Url', { AuthType: 'NONE' })
    t.hasResourceProperties('AWS::Lambda::Permission', {
      Action: 'lambda:InvokeFunction',
      InvokedViaFunctionUrl: true,
    })
    t.hasResourceProperties('AWS::Logs::LogGroup', { RetentionInDays: 1 })
    t.hasOutput('HelloUrl', { Value: Match.anyValue() })
  })
})
