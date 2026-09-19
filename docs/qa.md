# QA Baseline (FOOD-53)

QA reference for the Food kitchen app, written so cost-optimization PRs
(FOOD-54/55/56/57/58, all children of FOOD-45) can be gated against one rule:
**every cost change must leave the website as good or better.**

Snapshot: `main` @ `7fefdfc` (2026-09-12). Stack: Next.js 16 / React 19
frontend (`src/`), FastAPI backend (`backend/app/`), Postgres 16 in Docker
(port 5433), Anthropic Claude for receipts and meals, OpenAI `gpt-image-1`
for meal photos. `next.config.ts` proxies `/api/*` to FastAPI on :8000.

FOOD-58 Claude tier routing (policy, eval gates, flag rollout, when not to
downgrade): **[`backend/MODEL_ROUTING.md`](../backend/MODEL_ROUTING.md)**.
`MODEL_ROUTING_ENABLED` defaults **off**; do not flip it from this checklist.

---

## 1. How to run

### Run the app locally

Prerequisites: Node 20+, Python 3.11+, Docker, an Anthropic API key. OpenAI
and Resend keys are optional (see below).

```bash
# 1. dependencies
npm install
cd backend && python3 -m pip install -r requirements.txt && cd ..

# 2. .env in the repo root (backend reads it; see backend/app/config.py)
cat > .env <<'EOF'
DATABASE_URL="postgresql://postgres:postgres@localhost:5433/food"
AUTH_SECRET="replace-with-a-long-random-string"
ANTHROPIC_API_KEY="sk-ant-..."
OPENAI_API_KEY=""            # optional: AI meal photo when user skips upload
RESEND_API_KEY=""            # optional: password-reset email; reset URL is logged if empty
EMAIL_FROM="Food <onboarding@resend.dev>"
FRONTEND_URL="http://localhost:3000"
EOF

# 3. everything at once: Postgres (docker compose) + FastAPI :8000 + Next.js :3000
npm run dev
```

`npm run dev` = `docker compose up -d` → wait for tcp:5433 →
`uvicorn app.main:app --reload --port 8000` (in `backend/`) + `next dev`.
Tables are created and migrations applied on FastAPI startup
(`app/main.py` lifespan → `db_migrate.run_migrations`). Open
http://localhost:3000. Individually: `npm run db:up` / `npm run dev:api` /
`npm run dev:web`; stop the DB with `npm run db:down`.

Health check: `curl localhost:8000/api/health` → `{"status":"ok"}`.

### Run the tests

```bash
# Backend (SQLite per test, all AI providers mocked; no keys, no Docker)
cd backend
pytest                                   # full suite, 330 tests, ~27s
pytest tests/test_smoke_golden_path.py   # FOOD-53 golden path + cost baseline
pytest tests/test_meal_generator.py      # meal-generation call budget
pytest --cov=app --cov-report=term-missing

# Frontend (there are no frontend tests; CI runs these three)
npm run lint
npm run typecheck
npm run build
```

CI (`.github/workflows/ci.yml`) runs exactly these on every PR and push to
`main`: **Frontend** job = lint + typecheck + build; **Backend** job =
`pytest` with `ANTHROPIC_API_KEY=""` and `OPENAI_API_KEY=""`. An autouse
fixture in `backend/tests/conftest.py` blanks both keys as well, so any
unmocked AI call fails immediately rather than spending money.

---

## 2. Baseline smoke checklist (FOOD-53)

Run manually after every deploy (Amplify + App Runner) and before merging any
cost PR. The same steps run automatically against mocked Claude in
`backend/tests/test_smoke_golden_path.py::TestGoldenPathSmoke::test_signup_upload_generate_cookbook`.

| # | Step | Action | Expected | AI calls today |
| --- | --- | --- | --- | --- |
| 1 | **Signup** | `/login` → **Create account** with a fresh email and a password with upper, digit, symbol (e.g. `Sm0ke-Test!`) | Lands on the dashboard (tabs: Upload a receipt / View ingredients / Generate meal / View cookbook); `GET /api/auth/me` 200; session cookie is HttpOnly | 0 |
| 2 | **Upload receipt** | **Upload a receipt** → choose a real U.S. grocery receipt (jpg/png/webp/gif/pdf) → **Scan a receipt** | "Reading your receipt…" then within ~60s the review screen lists every food line with a plain-English name (abbreviations expanded), quantity, unit, calories/serving; non-food lines (tax, bags, coupons) absent; store name shown | 1 vision scan + 1 nutrition per food item |
| 2b | **Confirm** | Optionally edit a quantity → **Save ingredients** | Receipt status `completed`; **View ingredients** shows each item once with the edited quantity, unit, serving size, servings-per-unit, macros; uploading the same item again merges rather than duplicates | per item: 1 canonicalize + 1 unit check + 1 nutrition + 1 pantry match |
| 3 | **Generate meal** | **Generate meal** tab → **Generate meal** | Step "Suggest": one single-person meal with name, description, ingredients used (each ≤ pantry amount, in pantry units), numbered step-by-step instructions, macros with calories in **500–800 kcal**. **Try another suggestion** returns a differently named meal | 1–4 Sonnet calls per click |
| 4 | **Cookbook (own photo)** | Step "Cook" → upload a JPG/PNG/WEBP/GIF → **Save to cookbook** | "Meal saved to your cookbook."; **View cookbook** shows the entry with title, photo, macros, instructions; **View ingredients** shows quantities reduced by the amounts used (items fully used are removed); the draft meal is gone from Generate meal | 0 |
| 4b | **Cookbook (AI photo)** — requires `OPENAI_API_KEY` | Step "Cook" → leave photo empty → **Save to cookbook** | Same as 4 but with a generated photo | 1 Sonnet prompt-writer + 1 OpenAI image |
| 5 | **Negative** | Step 4b with no `OPENAI_API_KEY` | Error mentions the OpenAI key; cookbook unchanged; pantry not deducted; meal still shown so the user can retry with a photo | 0 |

Note: the UI has no "skip photo" control — saving without a photo always
calls OpenAI. `POST /api/meals/{id}/complete?skip_photo=true` (0 AI calls) is
API-only and is what the automated smoke uses. If the team wants a zero-cost
save path for users, that is a product change, not a QA one.

Record per step: pass/fail, wall-clock seconds for steps 2 and 3, and the
Claude call counts/tokens once FOOD-54 instrumentation exists. Anything
slower than `main` or a step that fails is a blocker for a cost PR.

The automated golden path uses a 2-item receipt (chicken breast 2 lb, white
rice 2 lb, one non-food line) and asserts: 3 Claude calls at upload, 8 at
confirm (all `RECEIPT_ANTHROPIC_MODEL`), 1 at generate
(`MEAL_ANTHROPIC_MODEL`), 0 at complete; meal = 774 kcal / 78 g protein;
pantry after cookbook = 1.5 lb chicken, 1.75 lb rice.

---

## 3. Current test inventory

All automated tests are backend pytest tests in `backend/tests/`. **There
are no frontend unit tests and no browser tests.**

| File | Tests | Level | Covers |
| --- | --- | --- | --- |
| `test_smoke_golden_path.py` (new) | 3 | HTTP e2e | FOOD-53 golden path with per-stage Claude call count + model pinned; skip-photo makes 0 AI calls; OpenAI-missing failure leaves cookbook/pantry untouched; empty pantry makes 0 calls. Uses a prompt-routed `FakeClaude` (reads `system` and user text) |
| `test_meal_generator.py` (new) | 14 | unit | JSON/instruction parsing; calorie retry loop (1 call in range, retry with feedback, 4-attempt cap, closest-meal fallback); same-meal avoidance; clamp to pantry; no-key/no-pantry guards |
| `test_receipt_to_inventory_e2e.py` | 4 | HTTP e2e | upload → edit draft → confirm → inventory; cancel; item removal; two receipts. **Ordered** mock list (`build_receipt_flow_side_effect`) — breaks if call order/count changes |
| `test_auth.py` | 21 | HTTP | register hashes password; login/logout cookie; expired/revoked sessions; password reset lifecycle; cross-user 404s |
| `test_ingredient_update.py` | 34 | HTTP + unit | `PATCH /api/ingredients/{id}` edits, conversions, auto-delete on depletion |
| `test_cookbook_transaction.py` | 10 | service | `add_meal_to_cookbook` atomicity (entry + deduction commit or roll back together) |
| `test_ingredient_deduction.py` | 93 | unit | unit parsing/conversion, serving math, deduction scenarios |
| `test_ingredient_merge.py` | 36 | unit | draft-row merge keys and quantity sums |
| `test_ingredient_normalization.py` | 70 | unit + mocked LLM | local canonical matching; `match_ingredient_to_pantry` payload handling; `create_ingredient` LLM merge vs manual |
| `test_ingredient_recognition.py` | 5 | unit (mocked LLM) | `resolve_item_nutrition` rejects unrecognized items |
| `test_unit_check.py` | 4 | unit (mocked LLM) | `check_ingredient_unit` |
| `test_validation.py` | 32 | unit | quantity/unit validation (`backend/VALIDATION_BEHAVIOR.md`) |
| `test_password_utils.py` | 5 | unit | password policy |
| `test_email.py` | 2 | unit (mocked Resend) | reset email |

Untested today: `meal_image.py` (prompt writer fallback, OpenAI error
mapping), `routers/cookbook.py` delete/photo, manual-add receipt path
(`test_receipt_flow_with_manual_additions` is commented out), all of `src/`.

---

## 4. Where to mock vs where a real eval is needed

Every Anthropic call is `anthropic.Anthropic(api_key=…).messages.create(...)`
created at call time, so `patch("anthropic.Anthropic")` covers all sites
(`receipt_analyzer.py`, `meal_generator.py`, `meal_image.py`). OpenAI is
`OpenAI(api_key=…).images.generate` in `meal_image.py`.

| Call site (`backend/app/services/…`) | Model | Mock in CI proves | Needs a real-model eval for |
| --- | --- | --- | --- |
| `receipt_analyzer.analyze_receipt_image` (image/PDF → items) | Opus, 4096 tok | JSON parsing, non-food filtering, error mapping, status lifecycle | **extraction accuracy**: item recall/precision, qty/unit, abbreviation expansion, store name — needs labelled real receipts |
| `receipt_analyzer.estimate_ingredient_nutrition` (per item, twice) | Opus, 1024 tok | field mapping, `recognized=false` rejection, servings-per-unit recompute | nutrition plausibility vs USDA, recognition false-positive/negative rate |
| `receipt_analyzer.check_ingredient_unit` | Opus, 256 tok | warning propagation | unit-plausibility judgement quality |
| `receipt_analyzer.match_ingredient_to_pantry` (2× per item at confirm) | Opus, 256 tok | id validation, ambiguity handling, canonical-name fallback | merge correctness (Organic Chicken Breast ≈ Chicken Breast; Brown ≠ White Rice) |
| `meal_generator.generate_meal_from_ingredients` | Sonnet, 4096 tok, ≤4 attempts | retry/fallback logic, clamping, instruction normalization, macros math | **meal quality**: kcal-in-range rate, pantry-limit violations, one-person portions, instruction coherence, variety on "try another" |
| `meal_image._build_image_prompt` + `generate_meal_image` | Sonnet 200 tok + gpt-image-1 | fallback prompt, error mapping (untested today) | image relevance (visual spot check) |

Rule: mocked tests are **plumbing** gates and run on every PR. Any PR that
changes model, prompt text, `max_tokens`, caching, batching, or adds an
OCR/deterministic stage must additionally run a real-model eval (FOOD-58's
harness once it exists; until then the manual checklist in §2 on ≥2 real
receipts) and paste results in the PR. Real-model evals never run in the PR
CI job; run them locally or in a scheduled job with a real key.

---

## 5. Gaps (prioritized)

| Pri | Gap | Impact on cost gating | Recommended fix |
| --- | --- | --- | --- |
| P0 | No labelled receipt set / no eval harness. Fixture image is `b"fake image data"`. | FOOD-55 (OCR-first) and FOOD-58 (routing) cannot prove "accuracy within tolerance". | `backend/evals/receipts/` with 10+ real U.S. receipts + expected items JSON; `evals/run_receipt_eval.py` scoring recall/precision/unit accuracy; `evals/run_meal_eval.py` over fixed pantries scoring kcal-in-range %, pantry violations, JSON validity. Gate: no metric drops > agreed tolerance vs `main`. |
| P0 | No browser smoke; frontend is lint/typecheck/build only. | Response-shape changes pass pytest but break the UI. | Playwright script of §2 against `npm run dev` with a `FAKE_CLAUDE=1`-style backend switch (or mocked network). Run on PRs labelled `cost`. |
| P1 | No per-call usage/latency capture in tests. | Can't assert token savings (FOOD-56 cache reads, FOOD-58 model mix). | Once FOOD-54 lands, expose the per-call log in `FakeClaude`/a test hook and assert `cache_read_input_tokens > 0` on the second call, model mix per stage, and stage latency budgets. |
| P1 | Ordered mocks in `test_receipt_to_inventory_e2e.py`. | Any batching/reordering (FOOD-55/57) breaks 4 tests for the wrong reason. | Move `FakeClaude` to `conftest.py`; migrate those tests to it. |
| P1 | No frontend unit tests (`src/lib/*.ts` are pure). | Display of AI output (macros, units) unguarded. | Vitest + Testing Library; start with `src/lib/`. |
| P2 | `meal_image.py` untested; cookbook router untested; manual-add receipt path test disabled. | Image is the most expensive per-click call. | Unit tests with mocked OpenAI; re-enable manual-add test via `FakeClaude`. |
| P2 | No coverage report in CI. | Blind spots on cost paths. | `pytest --cov=app --cov-report=xml` + threshold. |
| P3 | Draft PR #5 (duplicate cookbook completion) open and stale. | Double-submit could double-deduct. | Rebase or close. |

---

## 6. Recommended PR gate for cost tickets

A PR is a **cost PR** if it touches `backend/app/config.py`,
`backend/app/services/{receipt_analyzer,meal_generator,meal_image,
ingredients,anthropic_cache}.py`, `routers/{receipts,meals}.py`, or any
prompt string.

### Always (blocking)

1. CI green: lint, typecheck, build, `pytest`.
2. `tests/test_smoke_golden_path.py` and `tests/test_meal_generator.py`
   pass. If `EXPECTED_*_CALLS`, per-stage model assertions, or the attempt
   budget changed, the PR description shows before/after per stage and why.
3. No test deleted, skipped, or loosened to pass.
4. User-visible values pinned by the smoke test unchanged (draft fields,
   `servings_per_container`, meal kcal/macros, `ingredients_used` text,
   numbered instructions, cookbook entry, deduction amounts).
5. Manual §2 checklist on the PR branch with ≥2 real receipts (one 15+
   items); steps 2 and 3 not slower than `main`. Paste timings.

### Per ticket

| Ticket | What it changes | Extra gate |
| --- | --- | --- |
| **FOOD-54** Instrument usage + cost dashboard | Adds logging around every `messages.create`; no prompt/model change | Smoke call counts and models unchanged. Unit test that the logger tolerates `Mock` usage objects (tests use `Mock()` clients). Verify every call site in §4 emits workflow id / model / tokens / cache fields / latency — grep for `messages.create` and confirm each is wrapped. No new AI calls. |
| **FOOD-55** OCR-first receipt pipeline + LLM fallback | Replaces/precedes the vision scan; adds image downsampling, confidence gates, Haiku/Sonnet fallback, upload hashing | Receipt eval (P0) on labelled set: recall/precision/unit accuracy within tolerance vs `main`; report % receipts completing without vision-LLM. Smoke: `EXPECTED_UPLOAD_CALLS` must drop or stay, never rise; draft item shape unchanged; non-food filtering still works; duplicate upload of the same file makes 0 scan calls. Add e2e tests for (a) OCR success path, (b) low-confidence fallback path, (c) OCR failure → LLM. Status lifecycle (`processing → pending_review`) unchanged so the UI review screen still appears. Migrate ordered mocks first (P1). |
| **FOOD-56** Prompt caching (PR #12) | Moves stable prompt text into `system=[{cache_control: ephemeral}]`; variable content stays in `messages` | Smoke passes unchanged (the fake routes on `system` + user text). Assert exactly one cached system block per call and **no** `cache_control` inside `messages`; assert variable content (pantry list, item name, image) is in the user turn — `test_meal_generator.py::test_in_range_meal_uses_exactly_one_call` checks this for meals. Prefix must be byte-stable: no timestamps/ids/user data before the breakpoint. Real-key check: second identical call shows `cache_read_input_tokens > 0`. Nutrition/pantry-match outputs unchanged on the manual checklist (moving instructions to `system` can change model behaviour). |
| **FOOD-57** Batch API for offline reprocess / nightly regen | New batch submit/poll path for non-interactive work; interactive paths stay sync | Smoke unchanged (interactive path must not go through Batch). New tests: batch request payload per item, result-to-row mapping by `custom_id`, partial failure/retry, 1h TTL cache blocks only on the batch path. Latency budget for steps 2–3 in §2 unchanged. Any regenerated meal that reaches the cookbook must still satisfy the meal eval thresholds. |
| **FOOD-58** Model routing policy + eval harness | Cheaper models for classify/extract; escalation on low confidence; evals in CI/scheduled | Written policy: [`backend/MODEL_ROUTING.md`](../backend/MODEL_ROUTING.md) (tiers, per-call-site table, when not to downgrade, flag rollout). Smoke: assert the **model per call site** matches the policy table (replace the single-constant assertion with a per-prompt-type map; flag off = today's Opus/Sonnet defaults). Eval harness runs on the PR (mocked) and on a schedule (real key) with thresholds: receipt recall/precision, ingredient match rate, meal acceptability, escalation rate. Canary/shadow results attached before full cutover. Do not enable `MODEL_ROUTING_ENABLED` until the live eval table is pasted. |

### Suggested labels / automation

- Label cost PRs `cost`; require the two smoke files in the required checks
  and a "Cost baseline before/after" section in the PR template.
- Schedule the real-model eval nightly on `main` once FOOD-58 lands; post
  deltas to the FOOD-54 dashboard.

---

## Appendix: AI call map and cost shape today

```
signup            POST /api/auth/register                     0 AI calls
upload receipt    POST /api/receipts/upload                   1 vision scan + N nutrition (6-thread pool)
review/edit       PATCH /api/receipts/{id}/draft              0
confirm           POST /api/receipts/{id}/confirm             N canonicalize + N × (unit check + nutrition + pantry match)
generate meal     POST /api/meals/generate                    1–4 (retry until 500–800 kcal; conversation grows per retry)
add to cookbook   POST /api/meals/{id}/complete[?skip_photo]  0 with photo/skip; else 1 Sonnet + 1 OpenAI image
cookbook          GET  /api/cookbook                          0
manual add        POST /api/ingredients/manual                unit check + nutrition + pantry match = 3
unit check        POST /api/ingredients/unit-check            1
```

A receipt with N food items costs **5N + 1** Opus calls (N = 2 → 11;
N = 20 → 101). Nutrition is estimated twice per item (upload and confirm) and
pantry-match runs twice per item at confirm, even with an empty pantry. No
`system` prompts, caching, batching, or structured outputs exist on `main`
today (FOOD-56 PR #12 introduces the first `system` blocks). Models are fixed
constants in `backend/app/config.py`: `RECEIPT_ANTHROPIC_MODEL =
"claude-opus-5"`, `MEAL_ANTHROPIC_MODEL = "claude-sonnet-5"`,
`OPENAI_IMAGE_MODEL = "gpt-image-1"`.
