# QA Baseline

Audit of the Food app's testing landscape, written so a QA agent can gate
cost-optimization changes. Mandate: **every cost change must leave the website
as good or better.**

Snapshot date: 2026-09-12 (`main` @ `7fefdfc`, after PR #9 added CI).

---

## 1. App structure

| Layer | Tech | Entry points |
| --- | --- | --- |
| Frontend | Next.js 16 / React 19 / Tailwind 4 | `src/app/*` (pages), `src/components/*`, `src/lib/api.ts` |
| Backend | FastAPI (Python 3.12), SQLAlchemy 2 | `backend/app/main.py`, `backend/app/routers/*`, `backend/app/services/*` |
| DB | PostgreSQL 16 (Docker, port 5433); SQLite in tests | `backend/app/models.py`, `backend/app/db_migrate.py`, `prisma/` (schema only) |
| AI | Anthropic Claude (receipts + meals), OpenAI `gpt-image-1` (meal photos) | `backend/app/config.py` (models), `backend/app/services/{receipt_analyzer,meal_generator,meal_image}.py` |

The Next.js app proxies `/api/*` to FastAPI (`next.config.ts`, 300s proxy
timeout because receipt analysis is slow). Sessions are HttpOnly cookies.

### Main user flow and where the AI calls happen

```
signup            POST /api/auth/register                     0 AI calls
   │
upload receipt    POST /api/receipts/upload                   1 receipt scan (image → JSON)
   │                                                          + N nutrition estimates (1 per food item, 6 threads)
   │              → receipt.analysis_status = pending_review, draft_items returned
review/edit       PATCH /api/receipts/{id}/draft              0 AI calls
   │
confirm           POST /api/receipts/{id}/confirm             N pantry-match (canonicalize names)
   │                                                          + N × (unit check + nutrition estimate + pantry match)
   │              → Ingredient rows created/merged; status = completed
generate meal     POST /api/meals/generate                    1–4 meal generations (retry until 500–800 kcal)
   │              → one draft Meal row per user (previous draft deleted)
add to cookbook   POST /api/meals/{id}/complete[?skip_photo]  0 AI calls with photo or skip_photo
   │                                                          else 1 Claude prompt-writer + 1 OpenAI image
   │              → CookbookEntry created, pantry deducted, Meal row deleted (atomic)
cookbook          GET  /api/cookbook                          0 AI calls
```

Cost shape today (all Anthropic calls are plain `messages.create`, no
`system` prompt except the image-prompt writer, no caching, no batching,
no tool use/structured outputs):

| Stage | Model constant (`backend/app/config.py`) | Calls for a receipt with N food items |
| --- | --- | --- |
| Receipt scan | `RECEIPT_ANTHROPIC_MODEL` = `claude-opus-5` | 1 (`max_tokens=4096`, image/PDF in prompt) |
| Nutrition estimate | same | N at upload **+ N again at confirm** (`max_tokens=1024`) |
| Unit plausibility check | same | N at confirm (`max_tokens=256`) |
| Pantry match / canonical name | same | 2N at confirm (`max_tokens=256`, always called even for an empty pantry) |
| Meal generation | `MEAL_ANTHROPIC_MODEL` = `claude-sonnet-5` | 1–4 (`max_tokens=4096`; conversation grows on each retry) |
| Image prompt writer | `claude-sonnet-5` | 0–1 (`max_tokens=200`) |
| Meal image | `OPENAI_IMAGE_MODEL` = `gpt-image-1` | 0–1 (1024×1024, medium) |

**A 2-item receipt costs 11 Opus calls end to end (3 at upload, 8 at confirm).**
A 20-item receipt costs 101. The nutrition estimate is computed twice per item
(upload and confirm), and the pantry-match prompt runs twice per item at
confirm. These are the obvious cost levers and they are all exercised by the
golden-path smoke test below, which pins the current counts.

Other flows: manual ingredient add (`POST /api/ingredients/manual`: unit check
+ nutrition + pantry match = 3 calls), inline unit check
(`POST /api/ingredients/unit-check`: 1 call), quantity edit, forgot/reset
password (Resend email).

---

## 2. Existing tests

All tests are backend pytest tests in `backend/tests/`. Run time ≈ 27s.
There are **no frontend unit tests and no browser tests**.

| File | Tests | Level | Covers |
| --- | --- | --- | --- |
| `test_smoke_golden_path.py` **(new)** | 3 | HTTP e2e (TestClient) | signup → upload → confirm → generate → cookbook; per-stage Claude call counts and model; skip_photo makes 0 AI calls; failure without OpenAI key does not touch cookbook/pantry; empty pantry makes 0 calls |
| `test_meal_generator.py` **(new)** | 14 | unit | JSON/instructions parsing, calorie retry loop (1 call in range, retry with feedback, 4-attempt cap, closest-meal fallback), same-meal avoidance, clamp to pantry, no-key/no-pantry guards |
| `test_receipt_to_inventory_e2e.py` | 4 | HTTP e2e | upload → draft edit → confirm → inventory; cancel; item removal; two sequential receipts. Uses an **ordered** mock response list (`build_receipt_flow_side_effect` in `conftest.py`), which implicitly fails if the number of Claude calls changes |
| `test_auth.py` | 21 | HTTP | register hashes password, login/logout cookie, expired/revoked sessions, bearer rejected, password reset lifecycle, cross-user 404s on receipts/ingredients/meals/cookbook |
| `test_ingredient_update.py` | 34 | HTTP + unit | `PATCH /api/ingredients/{id}` quantity/unit edits, conversions, auto-delete on depletion |
| `test_cookbook_transaction.py` | 10 | service (in-memory SQLite) | `add_meal_to_cookbook` atomicity: entry + deduction commit together or roll back; re-adding does not double-deduct; depletion deletes row |
| `test_ingredient_deduction.py` | 93 | unit | unit parsing/normalization, weight/volume conversion, serving math, deduction scenarios |
| `test_ingredient_merge.py` | 36 | unit | draft-row merge keys and quantity summation |
| `test_ingredient_normalization.py` | 70 | unit + mocked LLM | local canonical keys/matching; `match_ingredient_to_pantry` parsing (invalid ids, ambiguous, fenced JSON); `create_ingredient` LLM merge vs manual path |
| `test_ingredient_recognition.py` | 5 | unit (mocked LLM) | `resolve_item_nutrition` rejects `recognized=false`, unit warnings |
| `test_unit_check.py` | 4 | unit (mocked LLM) | `check_ingredient_unit` payload handling |
| `test_validation.py` | 32 | unit | quantity/unit validation rules (see `backend/VALIDATION_BEHAVIOR.md`) |
| `test_password_utils.py` | 5 | unit | password policy |
| `test_email.py` | 2 | unit (mocked Resend) | reset email send / log fallback |

Shared fixtures (`backend/tests/conftest.py`): per-test SQLite DB, `client`
(FastAPI TestClient with migrations and reset email patched), `test_user`,
`auth_token`, `mock_receipt_image` (**bytes `b"fake image data"`, not a real
receipt**), `sample_receipt_response`, `sample_nutrition_estimates`,
`create_mock_anthropic_response`, `build_receipt_flow_side_effect`. An
autouse fixture blanks `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` so an unmocked
path fails fast instead of spending money.

### CI

`.github/workflows/ci.yml` runs on every PR and push to `main`:

| Job | Steps |
| --- | --- |
| Frontend | `npm ci` → `npm run lint` → `npm run typecheck` → `npm run build` |
| Backend | `pip install -r requirements.txt` → `pytest` (SQLite; provider keys set to `""`) |

Not in CI: coverage reporting (pytest-cov is installed but unused), any
browser test, any real-model eval, any cost/latency measurement.

---

## 3. Gaps vs the required smoke (signup → upload receipt → generate meal → cookbook)

Before this audit, no single test exercised `POST /api/meals/generate` or
`POST /api/meals/{id}/complete`; `meal_generator.py` and `meal_image.py` had
zero coverage. `test_smoke_golden_path.py` now closes the backend half.
Remaining gaps, prioritized:

| # | Gap | Why it matters for cost gating | Suggested fix |
| --- | --- | --- | --- |
| P0 | **No browser/UI smoke.** The Next.js app is only lint/typecheck/build-checked. A backend change that alters response shape (e.g. `draft_items`, `ingredients_used` text, `photo_url`) can break the UI while all pytest tests pass. | Cost work will touch response construction (batching, caching, model swaps change JSON fidelity). | Playwright smoke against `npm run dev` with FastAPI pointed at a fake-Claude mode or with the Anthropic client mocked via an env flag. Steps: register → upload fixture image → confirm → generate → skip photo → assert cookbook card. Run on PRs labelled `cost`. |
| P0 | **No real-model evals / golden receipts.** Receipt fixtures are fake bytes; all Claude output is canned. Nothing measures extraction accuracy, nutrition plausibility, or meal quality against real model output. | Model routing (Opus → Sonnet/Haiku), prompt trimming, and caching are exactly the changes that degrade quality silently. | Add `backend/evals/` with 5–10 real U.S. receipt images + hand-labelled expected items (name, qty, unit, is_food), a script that runs `analyze_receipt_image` and scores item recall/precision and unit accuracy, and a meal eval that checks kcal-in-range rate, pantry-limit violations, and JSON validity over a fixed pantry set. Runs manually / nightly with a real key, never on PR. |
| P1 | **Ordered-mock brittleness.** `build_receipt_flow_side_effect` hard-codes call order; the upload nutrition calls run in a 6-thread pool so ordering is only accidentally stable. | Any change to call order (e.g. batching nutrition into one call) breaks four tests for the wrong reason. | Migrate `test_receipt_to_inventory_e2e.py` to the prompt-routed `FakeClaude` in `test_smoke_golden_path.py` (move it to `conftest.py`). |
| P1 | **Frontend unit tests absent.** `src/lib/{ingredients,meals,units,validation,password}.ts` are pure functions with no tests. | Cheap to add; protects display logic that consumes AI output (macro rounding, unit labels). | Add Vitest + `@testing-library/react`; start with `src/lib/*`. |
| P1 | **No token/latency accounting.** Nothing records `usage.input_tokens`/`output_tokens` or wall time per stage. | Can't prove a cost PR saved anything or didn't slow the UI. | Log `message.usage` per call (structured log or in-memory counter exposed in tests); assert on it in the smoke test once available. |
| P2 | **`meal_image.py` untested.** Prompt-writer fallback, OpenAI error mapping, empty `b64_json`. | Image generation is the single most expensive per-click call. | Unit tests with mocked `OpenAI().images.generate` and mocked Claude prompt writer; assert fallback prompt used when Claude fails. |
| P2 | **Manual-add path in receipt flow disabled.** `test_receipt_flow_with_manual_additions` is commented out (`test_receipt_to_inventory_e2e.py:180`). | Manual items trigger 3 AI calls each. | Re-enable using `FakeClaude`. |
| P2 | **Coverage not reported.** | No visibility into which cost paths are untested. | `pytest --cov=app --cov-report=xml` in CI + Codecov/threshold. |
| P3 | **Open draft PR #5** ("Prevent duplicate cookbook completion") is unmerged; double-click on "Add to cookbook" may double-deduct at the HTTP layer. | Not cost-related; flagged for QA awareness. | Rebase/merge or close. |

---

## 4. Where cost work meets QA

| Cost lever | Code | Existing check | Recommended additional check |
| --- | --- | --- | --- |
| Model routing (Opus → cheaper for nutrition/unit/pantry prompts) | `config.py` constants; every `messages.create(model=…)` | Smoke test asserts each stage uses the *constant* for that stage, so a change to constants passes but a change to which constant a stage uses fails | Receipt eval (recall/precision per model); nutrition plausibility eval |
| Deduplicate nutrition estimates (computed at upload **and** confirm) | `receipt_analyzer._enrich_receipt_nutrition`, `services.ingredients.resolve_item_nutrition` | Smoke test pins 3 upload + 8 confirm calls for 2 items | Update `EXPECTED_*_CALLS` deliberately; assert draft nutrition equals stored nutrition |
| Skip pantry-match when pantry is empty (currently always called) | `services.ingredients._find_matching_pantry_item`, `canonicalize_draft_items` | Smoke test pins the count; `test_ingredient_normalization.py::TestCreateIngredientLlmMatch` covers merge semantics | Ensure canonical display names still expand abbreviations (`CHKN BRST` → `Chicken Breast`) — add a case to the eval |
| Batch per-item prompts into one call | same | Ordered mocks in `test_receipt_to_inventory_e2e.py` will break → migrate to `FakeClaude` first | Assert per-item results are still attributed to the right item (thread-pool ordering bug class) |
| Prompt caching (`cache_control` on a shared system prompt) | none today; no `system=` on receipt/meal prompts | `test_meal_generator.py` asserts there is currently no `system` kwarg — update when introducing one | Assert `cache_control` block present and prompt text unchanged; eval unchanged |
| Reduce `MEAL_GENERATION_ATTEMPTS` / retry conversation growth | `meal_generator.py` | `test_meal_generator.py::TestCallBudget` pins 1/2/4-call behaviour and closest-meal fallback | Meal eval: kcal-in-range rate must not drop |
| Cheaper/smaller images, or skip Claude prompt writer | `meal_image.py` | none | Add `meal_image` unit tests (P2) and a visual spot check |
| Batch API / async processing | none | none | Would change `analysis_status` lifecycle (`processing` → poll); needs a new e2e test and UI smoke |

Mocked-LLM tests prove **plumbing**, not **quality**. Any change to model,
prompt text, `max_tokens`, or temperature must also run the eval harness
(P0 gap) with a real key.

---

## 5. Running tests

Local:

```bash
# backend (no API keys or Docker needed; SQLite + mocked providers)
cd backend
python3 -m pip install -r requirements.txt
pytest                                  # full suite (~27s)
pytest tests/test_smoke_golden_path.py  # golden-path gate only
pytest tests/test_meal_generator.py     # meal call-budget gate only
pytest --cov=app --cov-report=term-missing

# frontend
npm ci
npm run lint        # 0 errors, ~10 warnings today
npm run typecheck
npm run build
```

Env vars: tests need **none**. The autouse fixture blanks
`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`; individual tests set a dummy key via
`monkeypatch` when they need the code path that requires one. Upload dirs are
redirected to `tmp_path` in the smoke test via `UPLOAD_DIR`, `MEAL_UPLOAD_DIR`,
`COOKBOOK_UPLOAD_DIR` (settings reload from env on every access). Other tests
write to `backend/uploads/` (gitignored).

Running the real app for manual QA needs `.env` with `DATABASE_URL`,
`AUTH_SECRET`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (optional if you always
upload a photo), `RESEND_API_KEY` (optional; reset links are logged), then
`npm run dev`. See `README.md`.

CI: `.github/workflows/ci.yml` — both jobs must be green.

---

## 6. Minimal QA gate for PRs that touch cost paths

A PR touches a cost path if it changes any of: `backend/app/config.py`,
`backend/app/services/receipt_analyzer.py`, `meal_generator.py`,
`meal_image.py`, `services/ingredients.py`, `routers/receipts.py`,
`routers/meals.py`, or any prompt string.

Required (blocking):

1. CI green (`lint`, `typecheck`, `build`, `pytest`).
2. `tests/test_smoke_golden_path.py` passes. If `EXPECTED_*_CALLS` or the
   per-stage model assertions had to change, the PR description states the
   before/after call counts and models per stage.
3. `tests/test_meal_generator.py::TestCallBudget` passes, or the new budget is
   stated in the PR.
4. No test was deleted or `skip`ped to make the suite pass.
5. User-visible outputs pinned by the smoke test are unchanged: draft item
   fields, pantry `servings_per_container`, meal `calories`/macros,
   `ingredients_used` text, numbered `instructions`, cookbook entry fields,
   pantry deduction amounts.

Required when the change touches model, prompt text, `max_tokens`, caching,
or batching (until the eval harness exists, do this manually with a real key
and paste results in the PR):

6. Upload at least 2 real U.S. receipts (one short, one 15+ items). Compare
   draft items against the receipt: every food line present, no non-food
   lines, quantities/units sane, abbreviations expanded.
7. Confirm, then generate 3 meals. Each must: use only pantry items, respect
   pantry maximums, land in 500–800 kcal (or explain), have numbered
   single-person instructions.
8. Add one meal to the cookbook with `skip_photo` and one with AI image;
   pantry deducts correctly; cookbook renders both.
9. Report per-stage wall-clock time; receipt analysis must stay under the
   300s proxy timeout with headroom, and should not be slower than `main`.

Recommended:

10. Record `usage.input_tokens`/`output_tokens` per stage before and after.
11. Run the browser smoke (once P0 is built) against the PR branch.
