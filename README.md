# Food

A kitchen app that turns grocery receipts into a tracked pantry, then uses AI to suggest meals you can cook with what you have.

## What it does

1. **Sign up / sign in** with email and password.
2. **Upload a receipt** (image or PDF). Claude reads the receipt, extracts food items and nutrition, and lets you review/edit before saving. **Receipt parsing currently supports U.S. grocery receipts only** (English product names and U.S.-style quantity/unit formatting).
3. **Add ingredients manually** if you prefer not to use a receipt.
4. **View ingredients** — see your pantry with quantities, units, and nutrition. Remove items you no longer have.
5. **Generate a meal** — Claude suggests one meal from your pantry (using only amounts you actually have), with ingredients, step-by-step instructions, and estimated macros.
6. **Proceed with meal** — open the meal page and add it to your cookbook. A photo is optional; if you skip it, OpenAI generates an image of the meal.
7. **Cookbook** — when you add a meal, it (plus photo and macros) is saved to your cookbook, and used ingredient quantities are deducted from your pantry (or removed if fully used).

## Stack

| Layer | Tech |
| --- | --- |
| Frontend | Next.js, React, Tailwind |
| Backend | FastAPI (Python) |
| Database | PostgreSQL (Docker, port **5433**) |
| AI | Anthropic Claude + OpenAI (meal images) |

## Prerequisites

- Node.js 20+
- Python 3.11+
- Docker (for Postgres)
- An [Anthropic API key](https://console.anthropic.com/)
- An [OpenAI API key](https://platform.openai.com/) (for AI meal images when no photo is uploaded)
- A [Resend API key](https://resend.com/) (for password-reset emails)

## Setup

1. **Install frontend dependencies**

```bash
npm install
```

2. **Install backend dependencies**

```bash
cd backend
python3 -m pip install -r requirements.txt
cd ..
```

3. **Create a `.env` file** in the project root:

```env
DATABASE_URL="postgresql://postgres:postgres@localhost:5433/food"
AUTH_SECRET="replace-with-a-long-random-string"
ANTHROPIC_API_KEY="your-anthropic-api-key"
OPENAI_API_KEY="your-openai-api-key"
RESEND_API_KEY="your-resend-api-key"
EMAIL_FROM="Food <onboarding@resend.dev>"
FRONTEND_URL="http://localhost:3000"
```

`AUTH_SECRET` peppers session and password-reset tokens. Generate a long random string and never commit it. Sessions are stored in HttpOnly cookies and expire after 7 days. Password-reset links expire after **10 minutes**; after that the reset page will not show the form.

`RESEND_API_KEY` is used to email password-reset links. For local development you can use Resend’s `onboarding@resend.dev` sender (`EMAIL_FROM`); messages only deliver to addresses verified in your Resend account. If `RESEND_API_KEY` is unset, the reset URL is logged in the API console instead of being emailed.

Receipt analysis uses Claude Opus; meal generation uses Claude Sonnet 5; meal images use OpenAI `gpt-image-1`.

### Upload storage (receipts, meal photos, cookbook photos)

Uploads go to **S3 when `UPLOADS_BUCKET` is set** and to **local disk otherwise**, so nothing extra is needed for local development:

| Env var | Default | Notes |
| --- | --- | --- |
| `UPLOADS_BUCKET` | unset | Name of the private S3 bucket. When set, objects are written under `receipts/`, `meals/` and `cookbook/` and the container filesystem is never used for uploads. In production this is set by Terraform and the App Runner instance role provides credentials; region comes from the standard `AWS_REGION` (no app-specific region setting). |
| `UPLOADS_SIGNED_URL_TTL_SECONDS` | `300` | Lifetime of the presigned S3 URLs that `/api/meals/{id}/photo` and `/api/cookbook/{id}/photo` redirect to. Set to `0` to stream bytes through the API instead of redirecting. |
| `UPLOAD_DIR`, `MEAL_UPLOAD_DIR`, `COOKBOOK_UPLOAD_DIR` | `uploads/receipts`, `uploads/meals`, `uploads/cookbook` | Local-disk fallback directories (relative to `backend/`), only used when `UPLOADS_BUCKET` is unset. |

The database stores stable object keys (`<prefix>/<user id>/<uuid>_<filename>`) in both modes. To exercise S3 mode locally, export `UPLOADS_BUCKET` plus normal AWS credentials (`AWS_PROFILE` or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) and `AWS_REGION`; the SDK's default resolution is used as-is.

## Run locally

From the project root:

```bash
npm run dev
```

This starts:

- Postgres via Docker Compose (port **5433**)
- FastAPI on [http://localhost:8000](http://localhost:8000)
- Next.js on [http://localhost:3000](http://localhost:3000)

Open [http://localhost:3000](http://localhost:3000), create an account, and start with **Upload a receipt** or add ingredients by hand.

### Useful scripts

| Command | What it does |
| --- | --- |
| `npm run dev` | Start DB + API + web |
| `npm run db:up` | Start Postgres only |
| `npm run db:down` | Stop Postgres |
| `npm run lint` | Run ESLint |
| `npm run typecheck` | Run TypeScript (`tsc --noEmit`) |
| `npm run build` | Production Next.js build |

## CI

Pull requests and pushes to `main` run GitHub Actions (`.github/workflows/ci.yml`):

| Job | Checks |
| --- | --- |
| **Frontend** | `npm run lint`, `npm run typecheck`, `npm run build` |
| **Backend** | `pytest` in `backend/` (SQLite test DB; Anthropic/OpenAI mocked) |

Jobs run in parallel. The workflow fails if lint, typecheck, build, or any test fails. AI provider API keys are cleared in CI; tests mock Anthropic (and related) clients so no real provider calls are made.

Local equivalents:

```bash
# Frontend
npm ci
npm run lint
npm run typecheck
npm run build

# Backend
cd backend
python3 -m pip install -r requirements.txt
pytest
```

See `backend/tests/README.md` for more on the test suite.

## Notes

- Receipt upload is **U.S.-only** for now. Non-U.S. receipts (for example EU metric pack sizes embedded in product names) may parse incorrectly.
- Receipt analysis can take up to a minute; wait for Claude to finish before expecting the review screen.
- Meal generation only uses food already in **View ingredients**, and never asks for more than you have on hand.
- Adding a meal to the cookbook updates pantry quantities. Upload your own photo, or skip and let OpenAI generate one.
