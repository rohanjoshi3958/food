# Unused: Prisma / NextAuth scaffolding

This folder is **not used at runtime**. Do not run `prisma migrate`, `prisma generate`, or treat this schema as the source of truth for auth or the database.

## Why it is here

The original Next.js scaffold included a Prisma schema and NextAuth-shaped models (`User`, `Account`, `Session`, `VerificationToken`). The app no longer uses Prisma or NextAuth:

- Runtime database access is **SQLAlchemy** (`backend/app/models.py`, `backend/app/database.py`).
- Auth is **FastAPI** (`backend/app/routers/auth.py`) with hashed cookies in `auth_sessions` and `password_reset_tokens`.
- There is no `prisma` or `next-auth` package in `package.json` / `package-lock.json`.
- Amplify’s frontend build is `npm ci` + `npm run build` only (see `infra/modules/amplify/main.tf`). CI does not run Prisma.

The files were moved out of the default `prisma/` path so a stray `prisma` CLI invocation cannot find them and so deploy/auth work is not confused by leftover NextAuth models.

## What was quarantined vs deleted

| Artifact | Action | Reason |
| --- | --- | --- |
| `schema.prisma` (User / Account / Session / VerificationToken) | Quarantined here | Clearly unused by application code, but the `"User"` table name is still the live SQLAlchemy table. Keeping the historical schema documents the leftover NextAuth table shapes (`Account`, `Session`, `VerificationToken`) that may exist in older local DBs if the init migration was applied. |
| `migrations/20260606200000_init` | Quarantined here | Historical only. Runtime schema changes go through `backend/app/db_migrate.py` and `Base.metadata.create_all`. |
| Generated Prisma client (`src/generated/prisma`) | Never committed; gitignore entry removed | No generate step exists. |

Nothing was deleted from a live auth or deploy path.

## How unused was verified

Repo-wide search (case-insensitive) found **no** runtime references to:

- `PrismaClient`, `@prisma/client`, `prisma generate`, or `from "prisma"`
- `next-auth`, `NextAuth`, `PrismaAdapter`, `getServerSession`, or `AuthOptions`

`package.json` has no Prisma/NextAuth dependencies or scripts. Frontend auth UI calls FastAPI via `src/lib/api.ts`. Backend tests use SQLAlchemy models only.

## Do not

- Re-home this folder to `prisma/`
- Run Prisma against any Food database (local or AWS)
- Treat `Account` / `Session` / `VerificationToken` as current auth tables
