# Food

A kitchen app that turns grocery receipts into a tracked pantry, then uses AI to suggest meals you can cook with what you have.

## What it does

1. **Sign up / sign in** with email and password.
2. **Upload a receipt** (image or PDF). Claude reads the receipt, extracts food items and nutrition, and lets you review/edit before saving.
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
ANTHROPIC_MODEL=claude-opus-4-8
OPENAI_API_KEY="your-openai-api-key"
OPENAI_IMAGE_MODEL=gpt-image-1
```

`AUTH_SECRET` is used to sign JWTs. Generate any long random string.

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

## Notes

- Receipt analysis can take up to a minute; wait for Claude to finish before expecting the review screen.
- Meal generation only uses food already in **View ingredients**, and never asks for more than you have on hand.
- Adding a meal to the cookbook updates pantry quantities. Upload your own photo, or skip and let OpenAI generate one.
