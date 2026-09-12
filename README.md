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

### Receipt pipeline flags (FOOD-55, default off)

By default every receipt upload goes to vision Claude Opus exactly as before. Two opt-in flags change that:

| Env var | Default | Effect |
| --- | --- | --- |
| `RECEIPT_ANALYSIS_CACHE` | `0` | Re-uploading byte-identical receipt bytes (same user) reuses the prior parse, skipping OCR/LLM calls. Path `cache`. |
| `RECEIPT_OCR_FIRST` | `0` | Run in-process Tesseract + a deterministic parser first; only escalate to an LLM when a confidence gate fails. Paths `ocr` → (`haiku` → `sonnet`, if configured) → `opus_baseline`. |

`RECEIPT_OCR_FIRST=1` needs the Tesseract binary on the API host:

```bash
# Debian/Ubuntu
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
# macOS
brew install tesseract
```

If the binary is missing the OCR rung reports `ocr_unavailable` and the upload falls through to the existing Opus path, so nothing breaks — it just isn't cheaper. Optional tuning (all `RECEIPT_OCR_*` settings in `backend/app/config.py`): `RECEIPT_OCR_MIN_CONFIDENCE` (default 60), `RECEIPT_OCR_MAX_MISSING_QTY_UNIT_RATIO` (0.5), `RECEIPT_OCR_TOTALS_TOLERANCE` (0.02), `RECEIPT_OCR_TEXT_FALLBACK_MODEL` / `RECEIPT_OCR_VISION_FALLBACK_MODEL` (empty = skip that rung; the vision rung then uses the Opus baseline model), `RECEIPT_OCR_TESSERACT_CMD` (binary path override).

The path that produced each receipt is stored in `receipts.analysis_path`. Per-upload metrics (path, tokens, confidence, latency) are not emitted yet; FOOD-54 will wire its helper at the marked call site in `backend/app/routers/receipts.py`. The labeled eval set that gates flipping the default lives in `backend/evals/receipts/`.

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
