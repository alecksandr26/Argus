# infra-argus — cheapest-possible AWS demo deployment (CDK)

Deploys `backend-argus` + `ui-argus` to AWS **pay-per-use**, plus a root_admin-only, on-demand
fleet simulator (with real OSRM road routes) that runs **10 minutes at a time and then turns
itself off**. Built for demos, not production. Typical bill: **about $0.30/month**.

See `CLAUDE.md` in this folder for why it's built this way.

## What gets deployed

| Piece | AWS service | Bills when |
|---|---|---|
| Backend API (the **unchanged** `src/backend-argus` image + Lambda Web Adapter) | Lambda (container image) + Function URL | a request runs (free tier covers demos) |
| Frontend (`src/ui-argus` static build) | private S3 bucket + CloudFront | almost never (free tier) |
| `/api/*` routing | the same CloudFront distribution → the Lambda | — |
| Simulator + OSRM | ECS **Fargate Spot** task, started/stopped from the UI | only during a 10-minute session |
| OSRM graph (~8.6 GB) | S3 | storage only (~$0.20/month) |
| Secrets | SSM Parameter Store (standard, SecureString) | free |
| Database | **MongoDB Atlas M0** (free; not created by CDK) | free |

Four stacks: `ArgusNetwork` (VPC, public subnets, **no NAT**), `ArgusSimulator` (cluster,
task, OSRM bucket, reaper), `ArgusApp` (Lambda, UI bucket, CloudFront), `ArgusGuardrails`
(budget, budget action, kill switch).

## Hard cost guardrails (non-negotiable)

| # | Requirement | Enforced by |
|---|---|---|
| R1 | Every simulator session is **exactly 10 minutes** from start. The API takes no duration (sending one is a 422); CDK refuses to synth a session longer than 10. | `backend-argus` `app/simulator_control.py`, `lib/config.ts` |
| R2 | **At most 1** simulator task at a time (409), checked against Mongo *and* ECS. | backend |
| R3 | **Monthly quota: 120 minutes = 12 sessions** (UTC month). Each start is charged 10 min up front, never refunded. 429 when used up. | backend (`SimulatorSession` in Mongo) |
| R4 | The simulator container runs under `timeout 10m` (clamped to 10 whatever is passed in), so it ends itself. | `lib/simulator-stack.ts` |
| R5 | **Reaper Lambda every minute** stops any simulator task older than 10 minutes, whatever the backend thinks. | `lambda/reaper.py` |
| R6 | **AWS Budget $3/month**: email at 50/80/100%; at 100% a budget action **denies `ecs:RunTask`** to the backend (no more simulator starts). | `lib/guardrails-stack.ts` |
| R7 | **No always-on paid resources**: no NAT Gateway, load balancer, EC2, RDS/DocumentDB, Secrets Manager, WAF, API Gateway, ECS Service, EFS. `npm test` fails if one appears. | `test/guardrails.test.ts` |
| R8 | Backend Lambda 512 MB / 10 s / concurrency 2; **> 20k invocations in an hour → kill switch** (backend throttled to 0, simulator stopped). | `lib/guardrails-stack.ts`, `lambda/kill_switch.py` |
| R9 | All logs kept 3 days. | CDK, tested |
| R10 | Manual panic button: `npm run kill` / `npm run unkill`. | `scripts/` |

## Monthly cost (us-east-1 list prices; other regions ~10–20% more)

Simulator task ≈ $0.021/hour (Fargate Spot 1 vCPU + 2 GB ≈ $0.016 + public IPv4 $0.005), so
**~$0.0035 per 10-minute session**.

| Item | Idle month | Demo month (6 sessions = 1 h) | **Max possible** (quota used: 12 sessions) |
|---|---|---|---|
| Fargate Spot + IPv4 | $0 | $0.02 | $0.05 |
| Lambda | $0 | $0 (free tier) | $0 (free tier) |
| S3 OSRM data (8.6 GB) | $0.20 | $0.20 | $0.20 |
| ECR images (~0.6 GB) | $0.06 | $0.06 | $0.06 |
| S3 UI + CloudFront | ~$0.01 | ~$0.01 | ~$0.01 |
| Reaper, budget, alarms, SNS, logs, SSM | $0 | $0 | $0 |
| Atlas M0 | $0 | $0 | $0 |
| **Total** | **≈ $0.27** | **≈ $0.29** | **≈ $0.32** |

`npm run osrm:remove` between demo periods drops the idle month to ~$0.07 (`npm run
osrm:upload` restores it). If the Spot price or region differs, the order of magnitude doesn't.

## Prerequisites (one time)

1. **AWS account** and the AWS CLI logged in: `aws configure sso` (or `aws configure`).
   Pick one region and use it for everything, e.g. `us-east-1`.
2. **Node.js ≥ 22.22** and **Docker** running (Docker Desktop with WSL integration enabled, if
   on WSL). The CDK CLI is installed by `npm install` here.
3. **MongoDB Atlas M0** (free) cluster, in AWS, in the same region. Create a database user, and
   under *Network Access* allow `0.0.0.0/0` (Lambda has no fixed IP without a NAT Gateway,
   which would cost ~$32/month). Copy the `mongodb+srv://...` connection string.
4. In this folder:
   ```bash
   npm install
   npx cdk bootstrap          # once per account/region; creates CDK's asset bucket + ECR repo
   npm run setup-secrets      # prompts for Mongo URI, JWT secret, root admin password -> SSM
   ```
   If ECS complains about its service-linked role on a brand-new account:
   `aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com`.

## Hello world first (recommended)

Before the real stacks, prove the account, region, login and CDK flow work with one throwaway
Lambda (`ArgusHello`, `lib/hello-stack.ts`). It costs $0 and needs no Docker, Atlas or secrets.

```bash
npm install
npx cdk bootstrap          # once per account/region
npm run hello:deploy       # prints HelloUrl at the end
curl <HelloUrl>            # {"message": "Hello from Argus", "region": "..."}
npm run hello:destroy      # delete it again
```

If this works, the real deploy below will reach AWS the same way.

## Deploy

```bash
npm run deploy -- -c alertEmail=you@example.com -c rootAdminEmail=you@example.com
npm run osrm:upload        # once: ~8.6 GB from src/simulator-argus/osrm-data (setup_osrm.sh output)
```

`deploy` builds and tests the backend image, deploys the four stacks, builds the UI against the
CloudFront URL, uploads it, and hits `/health`. It prints the site URL at the end. Confirm the
AWS budget/SNS subscription emails you'll receive.

- **Lambda concurrency limit**: brand-new accounts sometimes have a limit of 10, which can't
  reserve any. If the deploy fails with *"decreases account's UnreservedConcurrentExecution
  below its minimum value"*, add `-c backendReservedConcurrency=none` (the kill switch still
  works) or ask AWS Support to raise the limit (free).
- **OSRM data** must come from the same `osrm/osrm-backend` image the task runs (that's what
  `src/simulator-argus/scripts/setup_osrm.sh` uses). The data lives in the main checkout's
  `src/simulator-argus/osrm-data` (gitignored); point `OSRM_DATA_DIR=` at it if needed.

## Using the simulator

Log in as root_admin → **Live operations** → *Fleet simulator* card → flip the switch **On**.
It shows *Starting…* (~2–3 minutes while the OSRM data copies onto the task), then *On* with a
countdown, and turns itself off at 0:00. The card shows how many of the month's 12 sessions are
left. Turning it Off early is fine (the session still counts). Spot capacity can, rarely,
interrupt a session; just turn it on again.

Other roles don't see the card, and the API returns 403 for them.

## Day-to-day

| Command | What it does |
|---|---|
| `npm run deploy` | redeploy everything (same as above) |
| `npm test` | synth + the guardrail assertions (R1, R4–R9) |
| `npm run kill` | stop all simulator tasks + throttle the backend to 0 |
| `npm run unkill` | restore backend concurrency (`UNKILL_CONCURRENCY=none` for low-limit accounts) |
| `npm run osrm:upload` / `osrm:remove` | put / delete the OSRM data in S3 |
| `npm run destroy` | delete every stack (buckets included). Atlas and SSM params stay. |

**If the budget action fired** (you got the 100% email): simulator starts are denied. After
checking the bill, detach the `ArgusGuardrails-DenySimulatorStart…` policy from the backend's
Lambda role in the IAM console, or reset it in *Billing → Budgets → argus-demo-monthly →
Actions*.

## Clients pointing at the cloud

- **Browser**: the CloudFront URL (same origin for UI and `/api/*`).
- **ESP32 firmware** (`src/esp32-argus/.../config.h`): backend URL = the CloudFront URL (HTTPS).
  The first request after idle is a ~2–4 s Lambda cold start; keep the HTTP timeout ≥ 10 s.
- **Local simulator against the cloud** (instead of the on-demand task):
  `BACKEND_BASE_URL=https://<cloudfront> docker compose --profile simulator up simulator-argus`
  — but this bypasses R1–R5, so prefer the switch in the UI.

## Not included

OSRM for the product route/ETA feature (would need an always-on OSRM), a custom domain, CI/CD,
and a mobile app.
