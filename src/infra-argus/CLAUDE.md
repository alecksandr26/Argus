# CLAUDE.md — src/infra-argus

Why the AWS deployment is built the way it is. `README.md` here is the "how do I deploy"
guide, and it holds the authoritative R1–R10 cost-guardrail table. The top-level `CLAUDE.md`
covers how this fits the rest of Argus.

## The one requirement everything else follows from

**Cheapest possible infra for demos.** The user's explicit, repeated constraint: pay only when
something is used, keep the existing Docker containers, change the API code as little as
possible, and the simulator must run for a few minutes at a time and **never** be left running.
An earlier draft that let the simulator run freely was estimated at ~$49/month and rejected
outright. Every design choice below is the cheapest option that still works. Don't add
anything that bills while idle (NAT Gateway, ALB, RDS/DocumentDB, an ECS Service, EC2,
Secrets Manager, WAF) without the user agreeing to it. `test/guardrails.test.ts` fails if you do.

## Backend: unchanged image + Lambda Web Adapter

`docker/backend-lambda/Dockerfile` is `FROM argus/backend-argus:latest` (the real
`src/backend-argus` image, pytest gate included, built by `scripts/deploy.sh`) plus AWS's Lambda
Web Adapter extension, which proxies Lambda invocations to the same uvicorn process. No Mangum,
no handler, no changes to `backend-argus`'s code or Dockerfile for Lambda. Its Mongo client is
created once per execution environment in `lifespan`, so warm invocations reuse it.

Secrets: `load_ssm_env.py` fetches `/argus/*` SecureString params into env vars and then `exec`s
uvicorn. CloudFormation can't resolve `ssm-secure` into Lambda env vars, and passing secrets
as plain template parameters would leave them readable in the template and console. Doing it in
the wrapper keeps `backend-argus`'s settings code untouched (it just reads env vars).
SSM over Secrets Manager: standard params are free; Secrets Manager is $0.40/secret/month.

Function URL, not API Gateway: free, and nothing API Gateway adds is needed. `authType: NONE`
because the app authenticates every request itself (JWT / device key). The public URL is
capped by reserved concurrency 2 (R8) and the invocation alarm → kill switch. AWS has required
both `lambda:InvokeFunctionUrl` *and* `lambda:InvokeFunction` (with
`InvokedViaFunctionUrl`) for public function URLs since late 2025. Current `aws-cdk-lib` adds
both, and a test pins it, because a missing one gives a silent 403 on every request.

## One CloudFront distribution for UI + API

`/api/*` and `/health` go to the Function URL (`CACHING_DISABLED`,
`ALL_VIEWER_EXCEPT_HOST_HEADER`, because a Function URL rejects a foreign Host). Everything else
goes to the private UI bucket through OAC. This means:
- the browser is same-origin, so **no CORS** configuration is needed (`CORS_ORIGINS` stays at
  its default);
- the UI bundle and the API URL don't depend on each other at build time, so there's no
  stack-ordering loop. `deploy.sh` uploads `dist/` with `aws s3 sync` after the stacks exist,
  rather than using a `BucketDeployment` that would need the bundle at synth time.

The SPA fallback is a CloudFront **Function** on the UI behavior (paths without a `.` →
`/index.html`), **not** distribution-level `errorResponses`. Those would also rewrite the
API's real 404s into `200 index.html`, which breaks `ui-argus` (for example, `RouteLiveView`
treats a status 404 as "no telemetry yet"). A test pins this too.

## Simulator: one Fargate Spot task per session, controlled by the backend

The simulator is a long-running loop, and Lambda's 15-minute cap and per-ms billing don't fit
it. An ECS Service would bill 24/7. So the backend starts a **standalone task** with
`run_task` (capacity provider `FARGATE_SPOT`, ~70% cheaper than on-demand) when root_admin flips
the switch, and stops it after 10 minutes. The backend logic lives in
`src/backend-argus/app/simulator_control.py`. The task has three containers sharing one
network namespace, so the simulator reaches OSRM on `localhost`:
1. `osrm-data` (stock `aws-cli` image): `s3 sync` of the preprocessed graph onto a task volume,
   then exits. S3 rather than baking 9 GB into an image: $0.20 vs $0.95/month, and the stock
   images need no custom build.
2. `osrm` (stock `osrm/osrm-backend`, same image `setup_osrm.sh` preprocessed with, since the
   file format is version-specific), `--mmap` so 2 GB of RAM is enough. Its health check is a
   bash `/dev/tcp` probe, because the image has no curl.
3. `simulator`: the **unchanged** `src/simulator-argus` image (its own pytest gate runs during
   the asset build), started only once `osrm` is healthy, wrapped in `sh -c '… timeout "$m"m
   python -m simulator_argus'`. The minutes come from the backend's override but are clamped to
   the session cap in the shell too, so no override can lengthen a session. `"$m"m` rather
   than `"${m}m"`: CloudFormation's validator flags `${m}` as an unresolved parameter.

The backend tells the task where to post (`BACKEND_BASE_URL`) from its own request URL. Wiring
the Function URL into the task definition would make `ArgusSimulator` and `ArgusApp` depend on
each other.

Public subnet + `assignPublicIp` instead of private subnets + NAT: a NAT Gateway is ~$32/month
for something that runs a few minutes a month. The security group is egress-only.

## Defense in depth on "never left running"

Every layer works on its own, and each one alone caps the damage:
backend auto-off in `status()` → in-container `timeout` → **reaper every minute** (measured from
ECS `createdAt`, so it doesn't trust the backend) → monthly quota → **budget action** denying
`RunTask` → invocation-spike kill switch → manual `npm run kill`. The worst case if the backend
is completely broken is the reaper's 11 minutes per task, about $0.004.

## Testing

- `npm test`: synth all four stacks with fixed account/region and assert R1, R4–R9 plus the
  CloudFront/permission/secret wiring. No AWS access needed. It stages the simulator Docker
  context (excluding `osrm-data`) but doesn't build it.
- `lambda/test_lambdas.py`: the reaper and kill switch with stubbed boto3 clients (excluded
  from the Lambda asset bundles).
- The backend's own `tests/test_simulator.py` covers R1–R3 and root_admin-only access.

**Not verified in this repo yet**: an actual `cdk deploy` to a real AWS account (none was
available in the session that wrote this), so the first real deploy is also the first
end-to-end test of the adapter, OSRM-on-Fargate, Spot startup time, and the budget action. The
README's verification steps are the checklist for that run.
