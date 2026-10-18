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

## Step-by-step deploy (first time)

Run everything in a WSL/Linux terminal, from this folder (`src/infra-argus`), unless a step says
otherwise. Stop at any step that fails; later steps depend on it.

### Step 0 — Machine setup (once per machine)

1. **Node.js ≥ 22.22** (the UI's test tooling needs it; the CDK CLI itself is installed by
   `npm install` below):
   ```bash
   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
   nvm install 24
   node --version
   ```
2. **Docker**: Docker Desktop → *Settings → Resources → WSL integration* → enable your distro.
   `docker version` must work inside WSL (the deploy builds the backend and simulator images).
3. **AWS CLI logged in, one region for everything**:
   ```bash
   aws login                              # opens the browser; pick region us-east-1
   aws sts get-caller-identity            # must print your account id
   ```
   `aws login` gives **temporary** credentials (no long-lived keys to leak) and saves the
   region in `~/.aws/config`. They **expire after a few hours**. When any `aws`/`cdk` command
   says *"Your session has expired. Please reauthenticate using 'aws login'"*, just run
   `aws login` again. Alternatives: `aws configure sso` (needs IAM Identity Center enabled), or
   `aws configure` with access keys (works, but avoid: a leaked key works until you delete it).
   If you sign in as the account **root** user, turn on MFA for it (console → account menu →
   *Security credentials*), and prefer an admin IAM/Identity Center user for day-to-day work.
4. **MongoDB Atlas M0** (free): create a cluster on AWS in that region (or the nearest one Atlas
   offers M0 in), create a database user, and under *Network Access* allow `0.0.0.0/0` (Lambda
   has no fixed IP without a NAT Gateway, which would cost ~$32/month). Copy the
   `mongodb+srv://...` connection string.

### Step 1 — Install and bootstrap (once per account + region)

```bash
npm install
npx cdk bootstrap          # creates CDK's asset bucket + ECR repo in this account/region
```

On a brand-new account, if ECS later complains about its service-linked role:
`aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com`.

### Step 2 — Hello world ($0, recommended)

Proves the account, region, login and CDK flow work with one throwaway Lambda (`ArgusHello`,
`lib/hello-stack.ts`) before touching the real stacks. No Docker, Atlas or secrets needed.

```bash
npm run hello:deploy       # prints HelloUrl at the end
curl <HelloUrl>            # {"message": "Hello from Argus", "region": "..."}
npm run hello:destroy      # delete it again
```

If this fails, fix it first: the real deploy reaches AWS exactly the same way.

### Step 3 — Local `.env` + secrets to AWS (once; re-run to rotate)

Your secrets live in a **local, gitignored** file, `src/infra-argus/.env`. It's never committed
and never deployed. `npm run setup-secrets` copies the secrets from it into AWS SSM Parameter
Store (encrypted, free), and the deployed backend loads them from there at startup.

```bash
cp .env.example .env
nano .env                  # or: code .env
```

| `.env` variable | What to put | Goes to |
|---|---|---|
| `AWS_REGION` | `us-east-1` | used by the scripts |
| `MONGODB_URI` | Atlas *Connect → Drivers* string: `mongodb+srv://user:pass@cluster.xxxx.mongodb.net/?retryWrites=true&w=majority` | SSM `/argus/mongo-uri` |
| `JWT_SECRET` | any long random string: `openssl rand -hex 32` | SSM `/argus/jwt-secret` |
| `ROOT_ADMIN_PASSWORD` | **the password you'll log in with as root_admin** (the simulator uses it too) | SSM `/argus/root-admin-password` |
| `ROOT_ADMIN_EMAIL` | your root_admin login email | `cdk deploy -c rootAdminEmail` |
| `ALERT_EMAIL` | where budget/kill-switch alerts go (empty = none) | `cdk deploy -c alertEmail` |

Then push the secrets to AWS:

```bash
npm run setup-secrets      # prints "saved /argus/..." three times
```

- Quotes around values are fine, and so are Windows line endings.
- Anything left empty in `.env` is prompted for instead, with input hidden.
- A variable already exported in your shell overrides `.env`.
- Use a **letters-and-numbers-only** Atlas password; `@ : / ? # %` break the URI.
- To rotate a secret: edit `.env`, re-run `npm run setup-secrets`, then redeploy (Step 4) so the
  Lambda picks up the new value.
- **Never** paste the real `.env` contents into chats, issues, or commits. If a password leaks,
  change it in Atlas (*Security → Database Access → Edit Password*) and rotate.

### Step 4 — Deploy

```bash
npm run deploy
```

- `ROOT_ADMIN_EMAIL` and `ALERT_EMAIL` come from `.env`. A `-c` on the command line overrides
  them, e.g. `npm run deploy -- -c alertEmail=other@example.com`.
- `ALERT_EMAIL` receives the budget and kill-switch alerts. Confirm the subscription emails AWS
  sends you.
- What it does: builds and tests the backend image, deploys the four stacks, builds the UI
  against the CloudFront URL, uploads it, and hits `/health`. It ends with
  `Argus is live at: https://xxxx.cloudfront.net`. The first run takes ~10–15 minutes, mostly
  CloudFront.
- **Lambda concurrency limit**: brand-new accounts sometimes have a limit of 10, which can't
  reserve any. If the deploy fails with *"decreases account's UnreservedConcurrentExecution
  below its minimum value"*, add `-c backendReservedConcurrency=none` (the kill switch still
  works) or ask AWS Support to raise the limit (free).

### Step 5 — Test the root_admin user

The backend creates the root_admin account (`rootAdminEmail` + the Step 3 password) the first
time it starts, if no user with that email exists yet.

- **Browser**: open the CloudFront URL and log in. As root_admin, *Live operations* shows the
  *Fleet simulator* card (other roles don't see it).
- **curl**: the API takes the SHA-256 hex of the password, not the password itself:
  ```bash
  URL=https://xxxx.cloudfront.net
  curl $URL/health                       # {"status":"ok"}
  curl -X POST $URL/api/auth/login -H 'content-type: application/json' \
    -d "{\"email\":\"you@example.com\",\"password\":\"$(printf %s 'YOUR_PASSWORD' | sha256sum | cut -d' ' -f1)\"}"
  ```
  Expect an `access_token` and `"role": "root_admin"`. The first request after idle can take
  2–4 s (Lambda cold start).
- **Password rejected?** A user with that email already exists in that database, and startup
  never overwrites an existing account (by design). Use a fresh database or a different
  `rootAdminEmail`.

### Step 6 (optional) — Real road routes for the simulator

```bash
npm run osrm:upload        # once: ~8.6 GB from src/simulator-argus/osrm-data (setup_osrm.sh output)
```

Without it the simulator still works, with straight-line routes. The OSRM data must come from the
same `osrm/osrm-backend` image the task runs (that's what
`src/simulator-argus/scripts/setup_osrm.sh` uses). It lives in the main checkout's
`src/simulator-argus/osrm-data` (gitignored); point `OSRM_DATA_DIR=` at it if needed.

### Step 7 — Tear down when done

```bash
npm run destroy            # deletes all four stacks, buckets included
```

Atlas and the SSM secrets are kept. Redeploying later is just Step 4 again.

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

## Optional: let Claude Code inspect AWS and MongoDB (MCP servers)

Two MCP servers let Claude Code look at the deployed system directly (stacks, logs, costs,
collections, storage). They're configured per user in `~/.claude.json` (`-s user`), **never** in
the repo's `.mcp.json`, because they carry credentials.

| Server | What it gives Claude | Credentials it uses |
|---|---|---|
| `aws-mcp` | AWS API/docs access through `mcp-proxy-for-aws` | your current `aws login` session |
| `mongodb` | read-only queries, collection sizes, `db.stats()` | the connection string in its env var |

**Add them** (Node 24 + `uv` needed for `npx`/`uvx`):

```bash
# MongoDB (read-only): put the real connection string here yourself, never in chat or git
claude mcp add mongodb -s user \
  -e MDB_MCP_CONNECTION_STRING='mongodb+srv://<user>:<password>@<cluster>.mongodb.net/' \
  -- npx -y mongodb-mcp-server@latest --readOnly

# AWS (usually added already by the AWS CLI's MCP setup)
claude mcp add aws-mcp -s user \
  -- uvx mcp-proxy-for-aws@latest https://aws-mcp.us-east-1.api.aws/mcp

claude mcp list          # both should show ✔ Connected
```

Keep `--readOnly` on the MongoDB server: Claude can then read but never write, update or
delete. A database user with the *Only read any database* role adds a second layer.

**MCP servers only connect when Claude Code starts.** After adding one, or after fixing a
failure, restart Claude Code. The first start of each server downloads its package, which can
exceed the default 30 s startup limit, so give it more time:

```bash
MCP_TIMEOUT=60000 claude
```

**Troubleshooting**

| Symptom | Cause | Fix |
|---|---|---|
| `aws-mcp` → `-32602: Invalid request parameters` | the `aws login` session expired, so the proxy has no credentials | `aws login`, check `aws sts get-caller-identity`, restart Claude Code |
| `mongodb` → `connection timed out after 30000ms` | first `npx` download was slow | restart with `MCP_TIMEOUT=60000 claude` |
| `mongodb` connects but queries fail / TLS `alert number 80` | your IP isn't in Atlas *Network Access* | add `0.0.0.0/0`, wait until *Active* |
| `claude mcp list` is ✔ but the tools don't show up in the session | the session started before the server was ready | restart Claude Code |
| warning: *claude.ai connectors are disabled because ANTHROPIC_API_KEY…* | an API key in your shell overrides the claude.ai login | `unset ANTHROPIC_API_KEY` before `claude` if that's unintended (doesn't affect these two servers) |

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
