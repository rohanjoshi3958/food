# Food — AWS infrastructure (Terraform)

Terraform for the production AWS stack described in
[FOOD-49](https://linear.app/rjplayground/issue/FOOD-49/provision-aws-stack-amplify-app-runner-rds-s3-secrets-manager):

| # | Component | Module | What it creates |
| --- | --- | --- | --- |
| 1 | Network | `modules/network` | VPC, 2–3 public + private subnets, IGW, NAT gateway(s), S3 gateway endpoint, App Runner + RDS security groups |
| 2 | Database | `modules/database` | RDS **PostgreSQL 16** in the private subnets, encrypted, TLS enforced, generated master password |
| 3 | Backend | `modules/app_runner` | ECR repo, App Runner service (FastAPI) with VPC connector, `/api/health` check, IAM instance role, autoscaling, optional custom domain |
| 4 | Uploads | `modules/storage` | Private S3 bucket (public access blocked, ACLs disabled, SSE, versioning, TLS-only policy) + IAM policy scoped to `receipts/`, `meals/`, `cookbook/` |
| 5 | Secrets | `modules/secrets` | `food/prod/database_url`, `food/prod/auth_secret`, `food/prod/anthropic`, `food/prod/openai`, `food/prod/resend` |
| 6 | Frontend | `modules/amplify` | Amplify Hosting app (Next.js SSR / `WEB_COMPUTE`) + production branch, service role, optional custom domain |

```
Browser ──► Amplify Hosting (Next.js)              ──► App Runner (FastAPI :8000)
                │  BACKEND_URL env / optional /api/<*> rewrite      │ CORS_ORIGINS / FRONTEND_URL ◄── Amplify domain
                │                                                   │ VPC connector (private subnets)
                │                                                   ├─► RDS PostgreSQL 16   (SG: 5432 from App Runner only)
                │                                                   ├─► S3 uploads bucket   (gateway endpoint; receipts/ meals/ cookbook/)
                │                                                   ├─► Secrets Manager     (5 secrets → env vars at boot)
                │                                                   └─► NAT ──► Anthropic / OpenAI / Resend (443 only)
```

## Security model

- **RDS** is `publicly_accessible = false`, lives in private subnets, and its security group admits **only TCP/5432 from the App Runner security group**. `rds.force_ssl = 1` and the generated `DATABASE_URL` carries `?sslmode=require`.
- **App Runner** egresses through the VPC connector. Its security group allows **5432 → RDS SG** and **443 → anywhere** (AI APIs, Resend, S3, Secrets Manager); nothing else.
- **S3**: all four public-access blocks on, `BucketOwnerEnforced` (no ACLs), SSE-S3, TLS-only bucket policy. The instance role can `Get/Put/Delete` objects **only under `receipts/*`, `meals/*`, `cookbook/*`** and list only those prefixes.
- **Secrets**: the instance role may `GetSecretValue` on exactly the five ARNs. App Runner injects them as `DATABASE_URL`, `AUTH_SECRET`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `RESEND_API_KEY` (matching `backend/app/config.py`).
- **Nothing secret is in git.** The DB password and `AUTH_SECRET` are generated at apply time (they live in Terraform state and Secrets Manager). Third-party keys are seeded as `REPLACE_ME` and set out-of-band; Terraform ignores later changes to them. The GitHub token is a sensitive variable passed via the environment.

> Because state contains the RDS password and `DATABASE_URL`, use a remote, encrypted backend (`backend.tf.example`). Local state, `terraform.tfvars` and `backend.tf` are git-ignored.

## Prerequisites

- Terraform **>= 1.9** (validated with 1.16.2, AWS provider 6.x)
- AWS credentials with rights to create VPC/RDS/App Runner/Amplify/S3/IAM/Secrets Manager resources
- Docker (to build `backend/Dockerfile` from [PR #10](https://github.com/rohanjoshi3958/food/pull/10))
- For Amplify: install the **AWS Amplify GitHub App** on `rohanjoshi3958/food`, then create a GitHub PAT (classic: `repo`, `admin:repo_hook`) and export `TF_VAR_github_access_token`. The token is used once to wire the connection and is not stored by Terraform.

## Apply steps

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # edit as needed
cp backend.tf.example backend.tf               # point at your state bucket
export TF_VAR_github_access_token=ghp_...       # only needed while creating the Amplify app
terraform init
```

**1. First apply — everything except the App Runner service**

The ECR repository is empty at this point and App Runner cannot create a service from a missing image, so keep `create_app_runner_service = false` (the example tfvars default):

```bash
terraform apply
```

This creates the VPC, RDS, S3, secrets, IAM roles, VPC connector, ECR repo and the Amplify app.

**2. Build and push the backend image**

```bash
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region "$(terraform output -raw region)" \
  | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -t "$REPO:latest" ../backend
docker push "$REPO:latest"
```

**3. Set the real third-party secrets** (Terraform will not overwrite these):

```bash
aws secretsmanager put-secret-value --secret-id food/prod/anthropic --secret-string "$ANTHROPIC_API_KEY"
aws secretsmanager put-secret-value --secret-id food/prod/openai    --secret-string "$OPENAI_API_KEY"
aws secretsmanager put-secret-value --secret-id food/prod/resend    --secret-string "$RESEND_API_KEY"
```

`food/prod/database_url` and `food/prod/auth_secret` are already populated by Terraform.

**4. Second apply — create the App Runner service**

```bash
terraform apply -var create_app_runner_service=true   # or flip it in terraform.tfvars
terraform output app_runner_service_url
curl "$(terraform output -raw app_runner_service_url)/api/health"   # {"status":"ok"}
```

This apply also wires the URLs in both directions (FOOD-50 / FOOD-48): App Runner receives `FRONTEND_URL` and `CORS_ORIGINS` from the Amplify app's domain, and the Amplify production branch receives `BACKEND_URL` from App Runner. Verify with:

```bash
terraform output backend_frontend_url        # https://main.<app-id>.amplifyapp.com (or your custom domain)
terraform output backend_cors_origins        # includes the Amplify domain
terraform output backend_runtime_secret_env_vars
```

**5. Deploy the frontend.** Once `main` is pushed (or via *Run build* in the Amplify console) the frontend builds with `BACKEND_URL` set and deploys automatically.

## Required / notable variables

| Variable | Default | Notes |
| --- | --- | --- |
| `region` | `us-east-1` | |
| `project` / `environment` | `food` / `prod` | Drives names and the `food/prod/*` secret paths |
| `github_access_token` | `null` | **Required when creating the Amplify app**; pass via `TF_VAR_github_access_token` |
| `github_repository` | `https://github.com/rohanjoshi3958/food` | |
| `amplify_branch` | `main` | Production branch |
| `create_app_runner_service` | `true` | Set `false` on the first apply (empty ECR) |
| `backend_image_identifier` / `backend_image_tag` | `null` / `latest` | Defaults to the Terraform-managed ECR repo |
| `backend_cpu` / `backend_memory` | `1024` / `2048` | App Runner instance size |
| `frontend_url` | `null` | Override for `FRONTEND_URL`; default is the Amplify custom domain if set, else the Amplify default branch URL |
| `additional_cors_origins` | `[]` | Extra origins appended to `CORS_ORIGINS` (Amplify default domain / custom domain are always included) |
| `amplify_environment_variables` / `amplify_branch_environment_variables` | `{}` | Extra Amplify env vars (app-level must not reference App Runner; branch-level may) |
| `frontend_custom_domain` / `frontend_custom_domain_prefix` | `null` / `""` | Amplify domain association (you create the DNS records from the outputs) |
| `backend_custom_domain` | `null` | e.g. `api.example.com`; App Runner custom domain association |
| `amplify_enable_api_rewrite` | `false` | FOOD-48 option B, see caveats |
| `db_instance_class`, `db_multi_az`, `db_deletion_protection` | `db.t4g.micro`, `false`, `true` | |
| `single_nat_gateway` | `true` | One NAT for all AZs (cheaper) |
| `uploads_bucket_name` | `null` | Defaults to `food-prod-uploads-<account-id>` |

Full list with descriptions: `variables.tf` and each `modules/*/variables.tf`.

## Outputs

| Output | Description |
| --- | --- |
| `amplify_default_domain`, `amplify_branch_url`, `frontend_url` | Amplify domain / URL (custom domain wins when configured) |
| `app_runner_service_url`, `backend_url` | App Runner URL / the URL the frontend should call |
| `backend_frontend_url`, `backend_cors_origins`, `backend_runtime_secret_env_vars` | The FOOD-50 env wiring App Runner actually received |
| `rds_endpoint`, `rds_address`, `rds_database_name` | Database endpoint (private) |
| `uploads_bucket_name`, `uploads_bucket_arn` | S3 bucket |
| `secret_arns`, `secret_names` | The five Secrets Manager secrets |
| `ecr_repository_url`, `backend_image_identifier` | Where to push the backend image |
| `backend_custom_domain_*`, `amplify_domain_verification_record` | DNS records to create for custom domains |
| `nat_gateway_public_ips` | Backend egress IPs |

## Runtime environment given to FastAPI (FOOD-50)

Plain env vars set by Terraform (`modules/app_runner`, names from `backend/app/config.py`):

| Env var | Value | Source |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | fixed |
| `COOKIE_SECURE` | `true` | fixed (see cookie note below) |
| `FRONTEND_URL` | `https://main.<app-id>.amplifyapp.com`, or the Amplify custom domain, or `var.frontend_url` | Amplify module output → App Runner |
| `CORS_ORIGINS` | comma-joined: Amplify default branch URL + custom domain (if any) + `frontend_url` override (if any) + `additional_cors_origins` | Amplify module output → App Runner |
| `EMAIL_FROM` | `var.email_from` | variable |
| `UPLOADS_BUCKET` | S3 bucket name. When set, the API stores receipts / meal photos / cookbook photos under `receipts/`, `meals/`, `cookbook/` (FOOD-47) and serves photos via presigned GET URLs; when unset it falls back to local disk. Region comes from `AWS_REGION` (injected by App Runner), credentials from the instance role. | storage module |

Secrets injected by App Runner from Secrets Manager at start (`runtime_environment_secrets`, instance role has `GetSecretValue` on exactly these ARNs):

| Env var | Secret |
| --- | --- |
| `DATABASE_URL` | `food/prod/database_url` (Terraform-managed) |
| `AUTH_SECRET` | `food/prod/auth_secret` (generated once) |
| `ANTHROPIC_API_KEY` | `food/prod/anthropic` (set out-of-band) |
| `OPENAI_API_KEY` | `food/prod/openai` (set out-of-band) |
| `RESEND_API_KEY` | `food/prod/resend` (set out-of-band) |

Extra plain vars: `backend_extra_environment`. `terraform output backend_frontend_url backend_cors_origins backend_runtime_secret_env_vars` shows the resolved wiring.

## Request timeout and health check (FOOD-51)

**Architecture decision:** the backend stays on App Runner, and long-running receipt/AI work is handled **asynchronously** rather than as a ≥300 s synchronous request. The original ≥300 s target in FOOD-49/FOOD-51 is therefore superseded.

- **App Runner enforces a fixed 120-second synchronous request timeout** (read + process + write). It is not exposed in the API, console or Terraform ([AWS docs](https://docs.aws.amazon.com/apprunner/latest/dg/develop.html#develop.considerations), [roadmap #104](https://github.com/aws/apprunner-roadmap/issues/104)). This is the expected, documented ceiling for any single request to the API; the service already runs at the App Runner maximum, and nothing in the container lowers it (uvicorn has no request timeout).
- **Long AI work is async by design:** the receipt-analysis and meal-generation endpoints should enqueue a job and return immediately (job id), with the client polling a status endpoint (or SSE well under 120 s). No additional infrastructure is required for that pattern beyond what is here; if a dedicated worker or queue is introduced later it can reuse the network, RDS, S3 and secrets modules unchanged.
- **Health check:** `/api/health` over HTTP, 10 s interval, 5 s timeout, 1 healthy / 5 unhealthy thresholds (`modules/app_runner`, variables `health_check_*`).

FOOD-51 infra acceptance:

| Item | Status |
| --- | --- |
| Health check against `/api/health` configured | Done (`health_check_configuration` in `modules/app_runner/main.tf`) |
| Request timeout set to the App Runner maximum | Done — 120 s is the platform maximum and cannot be raised |
| 120 s limit documented | This section, `modules/app_runner/main.tf`, and the FOOD-48 notes below |
| Long receipt/AI work not cut off | Handled at the application level via async jobs, not by the sync request timeout |

## Notes for FOOD-48 — Next.js `/api/*` → App Runner

`next.config.ts` currently rewrites `/api/:path*` to `http://localhost:8000`. Terraform gives the Amplify **production branch** a **`BACKEND_URL`** environment variable (custom API domain if set, otherwise the App Runner URL; see output `backend_url`). The build spec also copies `BACKEND_URL` and `NEXT_PUBLIC_*` into `.env.production` so they are available to SSR at runtime, not just at build.

`BACKEND_URL` lives on the branch rather than the app deliberately: the App Runner service reads the Amplify *app's* default domain for `CORS_ORIGINS`/`FRONTEND_URL`, so the dependency chain is `aws_amplify_app → aws_apprunner_service → aws_amplify_branch`. Anything that would make the Amplify **app** depend on App Runner (app-level env vars, custom rules) would create a cycle — which is why the edge rewrite below requires a statically known `backend_custom_domain`.

**Rule of thumb:** the Amplify rewrite is fine for short CRUD calls. **Long AI work must not rely on the Amplify SSR proxy** — it goes through async jobs (see FOOD-51 above), so every request that passes through Amplify is a quick enqueue or a status poll.

- **Option A (recommended) — Next.js rewrite driven by env:**
  ```ts
  const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
  rewrites: async () => [{ source: "/api/:path*", destination: `${backend}/api/:path*` }]
  ```
  Same-origin from the browser's point of view, so cookies "just work". The rewrite runs in Amplify's SSR compute, which has a **~30 s hard limit** ([amplify-hosting #3508](https://github.com/aws-amplify/amplify-hosting/issues/3508)) — acceptable for CRUD, job submission and polling; never route a synchronous AI call through it.
- **Option B — Amplify edge rewrite:** set `amplify_enable_api_rewrite = true` (requires `backend_custom_domain`) to add a `200` custom rule `/api/<*> → https://<backend_custom_domain>/api/<*>`. This bypasses SSR compute but still goes through Amplify's CloudFront proxy, which has similar (~30 s) timeout behaviour and rewrites `Host`; test cookie behaviour before relying on it. Same short-request rule applies.
- **Option C — direct calls to App Runner:** only needed if some request must use the full App Runner 120 s window (e.g. a large upload). Point that `fetch` at `NEXT_PUBLIC_API_URL` (add it via `amplify_environment_variables`) with `credentials: "include"`; requires the CORS/cookie work in FOOD-50. Putting the API on a sibling subdomain (`backend_custom_domain = "api.example.com"` next to `app.example.com`) keeps the session cookie same-site so no `SameSite=None` changes are needed.

With the async-job design, Option A alone should cover the app; keep Option C in reserve.

## FOOD-50 — production env: ENVIRONMENT, CORS, FRONTEND_URL, COOKIE_SECURE, secrets

Infra side of [FOOD-50](https://linear.app/rjplayground/issue/FOOD-50/wire-production-env-environment-cors-frontend-url-cookie-secure). Everything below is wired by `main.tf` → `module.app_runner.environment_variables` / `runtime_secrets`.

| FOOD-50 item | How it is satisfied |
| --- | --- |
| `ENVIRONMENT=production` | Fixed env var on the App Runner service |
| `CORS_ORIGINS` includes the Amplify domain | Built from the Amplify module output: `https://<branch>.<app-id>.amplifyapp.com` always, plus the Amplify custom domain when `frontend_custom_domain` is set, plus `frontend_url`/`additional_cors_origins`. Single apply, no manual copy-paste. |
| `FRONTEND_URL` set correctly | Amplify custom domain if configured, else the Amplify default branch URL; `var.frontend_url` overrides. Used by the app for password-reset links. |
| `COOKIE_SECURE` / secure cookies in prod | See below |
| Secrets from Secrets Manager | `DATABASE_URL`, `AUTH_SECRET`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `RESEND_API_KEY` injected via `runtime_environment_secrets`; instance role scoped to those five ARNs |

**Cookie security.** `backend/app/config.py` computes `session_cookie_secure = cookie_secure or environment == "production"`. Setting `ENVIRONMENT=production` alone therefore makes session cookies `Secure`; Terraform *also* sets the explicit `COOKIE_SECURE=true` flag the app reads, so cookies stay secure even if `ENVIRONMENT` is ever changed (e.g. to `staging`). Both are plain env vars, not secrets.

**Cross-origin calls.** With the Next.js rewrite (FOOD-48 option A) the browser only talks to the Amplify origin, so cookies are first-party and `CORS_ORIGINS` is a defence-in-depth setting. If the browser ever calls App Runner directly (option C), the FastAPI CORS middleware must run with `allow_credentials=True`, the request must use `credentials: "include"`, and the session cookie needs `SameSite=None; Secure` unless the API shares a registrable domain with the frontend (`backend_custom_domain = api.example.com` next to `app.example.com`).

**Operational notes.**

- Adding an origin (preview branch, second domain): `additional_cors_origins`, then `terraform apply` — App Runner redeploys on env-var changes.
- Secrets are read once at App Runner start; after rotating `food/prod/*` values trigger a deployment (`aws apprunner start-deployment --service-arn ...`) or push a new image.
- `EMAIL_FROM` must be a verified Resend sender in production.
- Acceptance "app boots in prod with secrets from Secrets Manager": `curl $(terraform output -raw app_runner_service_url)/api/health` after step 4; the service will not reach `RUNNING` if any referenced secret is missing, and the three placeholder keys must hold real values for AI/email features to work.
- Long receipt/AI requests are being moved to async jobs in FOOD-59 (app work); nothing in this env wiring changes for that.

## Cost / operational notes

- The NAT gateway (~$32/mo + data) is the main fixed cost besides RDS and App Runner's `min_size = 1`. `single_nat_gateway = true` by default.
- RDS has `deletion_protection = true` and a final snapshot; set `db_deletion_protection = false`, `db_skip_final_snapshot = true`, `uploads_force_destroy = true`, `secrets_recovery_window_in_days = 0` for a throwaway environment.
- Amplify Hosting compute officially documents Next.js 12–15; this repo is on Next.js 16 — confirm support (or the required build image) on the first Amplify build.

## Validation

```bash
cd infra
terraform fmt -recursive -check
terraform init -backend=false
terraform validate
```

Both `fmt` and `validate` pass with Terraform 1.16.2 / AWS provider 6.64. No `plan`/`apply` has been run against a real account from CI.
