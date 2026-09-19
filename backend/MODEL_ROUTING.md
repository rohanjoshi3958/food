# Model routing policy for Claude call sites (FOOD-58)

Follow-up to FOOD-45 (reduce Claude token cost). Goal: send simple steps to
cheaper tiers and keep Sonnet/Opus for the reasoning that drives user-visible
quality, with evals as the gate for every downgrade.

The code for this policy is `app/services/model_router.py` (the `POLICY`
table and `route_model()`); the gate is `tests/evals/` (see its README). This
document is the human-readable source of truth — keep the two in sync.

## Tiers

| Tier | Model id (`app/config.py`) | Input / output $ per MTok | Cache min | Notes |
| --- | --- | --- | --- | --- |
| Opus | `OPUS_ANTHROPIC_MODEL = claude-opus-5` | 5 / 25 | 512 tokens | Vision extract, nutrition, escalation target |
| Sonnet | `SONNET_ANTHROPIC_MODEL = claude-sonnet-5` | 2 / 10 | 1,024 tokens | Constrained planning, ambiguous ingredient judgement |
| Haiku | `HAIKU_ANTHROPIC_MODEL = claude-haiku-4-5` (alias `RECEIPT_HAIKU_MODEL`) | 1 / 5 | 4,096 tokens | Classify / extract over text, soft OCR cleanup, cosmetic text |

Prices and cache minimums from the Anthropic docs as of this writing; check
the [models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
and [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
pages before relying on them. `claude-haiku-4-5` is the current Haiku alias
(there is no Haiku 5 yet); its retirement is "not sooner than October 15,
2026", so revisit the constant when a newer Haiku ships.

`RECEIPT_ANTHROPIC_MODEL` and `MEAL_ANTHROPIC_MODEL` still exist and still
point at Opus and Sonnet. They are the *defaults* — what every call site uses
while routing is off — and are what `tests/test_prompt_caching.py` pins.

## Policy by workflow

| Workflow | Tier | Why |
| --- | --- | --- |
| Classify / extract over text already in hand (food vs non-food, unit plausibility, field pull-out from OCR text) | **Haiku** | Short, closed-form answers; errors are cheap to detect and escalate. |
| Soft OCR text cleanup (fix character errors, split lines, expand obvious abbreviations) | **Haiku** | Cosmetic; downstream steps re-validate. Import `RECEIPT_HAIKU_MODEL`. |
| Ambiguous ingredient judgement (is this the same food as a pantry row, given qualifiers and units) | **Sonnet**, escalate to Opus | Needs world knowledge about foods; a wrong merge corrupts the pantry. |
| Constrained meal plan (calorie band, pantry maxima, single portion, avoid repeats) | **Sonnet** | Multi-constraint planning; the retry loop already costs 1–4 calls, a weaker model raises that. |
| Receipt vision extraction (image / PDF → line items) | **Opus** until evals green | Every downstream metric depends on it; no labelled image set yet. |
| Nutrition estimate per item | **Opus** until evals green | Numeric recall; a wrong kcal-per-serving silently skews meals. |

### Per call site

`default` is what ships (flag off). `routed` applies with
`MODEL_ROUTING_ENABLED=true`. `escalate_to` is the re-run tier for a
low-confidence answer (routing on only).

| `call_site` | default | routed | escalate_to | Low-confidence signal | Gate before flipping |
| --- | --- | --- | --- | --- | --- |
| `receipt.analyze_image` | Opus | Opus | — | — | Live `test_live_receipt_parse` green on the cheaper tier with a labelled image set (FOOD-53 gap). |
| `receipt.nutrition_estimate` | Opus | Opus | — | — | Nutrition-value fixtures + live run green. |
| `receipt.unit_check` | Opus | **Haiku** | Opus | `unit_plausible: false` (a rejection blocks the user, so confirm it) | Mocked evals already cover the wiring; live run with `FOOD_EVAL_MODEL=claude-haiku-4-5` before enabling. |
| `receipt.pantry_match` | Opus | **Sonnet** | Opus | `ambiguous: true`, a `match_id` not in the offered pantry, a missing/`ambiguous` that is not a bool, or a non-JSON answer | `ingredient_match` live run on Sonnet ≥ baselines. |
| `receipt.ocr_text_cleanup` | Haiku | Haiku | Sonnet | FOOD-55 live: Haiku cleans Tesseract text when `RECEIPT_OCR_FIRST` is on. The pipeline's next rung is Sonnet vision (`RECEIPT_OCR_VISION_FALLBACK_MODEL`), not `escalation_for()`. | OCR-first evals in `backend/evals/receipts`. |
| `receipt.ocr_cleanup` | Haiku | Haiku | Sonnet | FOOD-58 alias for `receipt.ocr_text_cleanup` (the name reserved before FOOD-55 landed). | — |
| `receipt.classify_text` | Haiku | Haiku | Sonnet | reserved; FOOD-55 uses the deterministic parser instead of an LLM classify step. | — |
| `meal.generate` | Sonnet | Sonnet | — | — | Not a downgrade candidate. |
| `meal.image_prompt` | Sonnet | **Haiku** | — | — (deterministic fallback exists) | None needed; cosmetic. |

`tests/test_model_router.py` asserts that every `call_site="…"` string in the
services has a row here and that every `default` equals the pre-routing
model.

## When NOT to downgrade

- **Anything that decides what goes into the pantry.** Vision extraction,
  nutrition values, and a *confident* pantry merge all write rows the user
  has to live with. Cheaper tiers may propose; a stronger tier must confirm
  anything that would merge into an existing row or block the user.
- **Meal generation.** Sonnet is the floor. A Haiku plan that misses the
  calorie band costs a retry (a second full call with the conversation so
  far), which erases the price difference and adds latency.
- **When the eval for that call site is not green on the cheaper tier.**
  Run `FOOD_EVAL_LIVE=1 FOOD_EVAL_MODEL=<tier> pytest tests/evals -m live`
  and paste the summary table into the PR that changes the policy.
- **When there is no confidence signal.** Escalation only works if the cheap
  tier's answer exposes uncertainty (an `ambiguous` flag, a negative
  classification, a schema miss). If a call site returns a bare value with
  no way to tell a guess from a fact, it stays on its current tier.
- **When prompt caching is doing the work.** Haiku's minimum cacheable
  prefix is 4,096 tokens; none of our prefixes reach it, so a Haiku call
  never gets cache reads. That is still cheaper than a cached Opus call
  today, but do not move a call site to Haiku *because* of caching, and
  re-check the arithmetic if Opus/Sonnet prefixes grow past their minimums
  (see `PROMPT_CACHING.md`).
- **Interactive paths never move to the Batch API** for cost reasons; that is
  FOOD-57's decision, not routing's.

## Escalation

A low-confidence answer is re-asked once on `escalate_to`, with the same
prompt. It is a second paid call, so it only pays off when low-confidence
answers are the minority. With routing on, the mocked corpus shows the trade
(`ingredient_match_routing_on` in `tests/evals/baselines.json`): Opus handles
~28% of pantry-match calls instead of 100%, at ~1.29 calls per item instead
of 1.0. Watch `reason=escalated` in the logs; if the escalation rate on a
call site climbs past roughly a third, the cheap tier is not earning its
place there.

Escalation never happens with routing off (`escalation_for()` returns
`None` whenever `routing_enabled` is false), so the flag's off state is
byte-for-byte the pre-FOOD-58 request pattern.

## Feature flag and rollout

- `MODEL_ROUTING_ENABLED` (env / `.env`, default `false`) — read at request
  time through `settings`. Nothing in a cached prompt prefix depends on it
  (model id is not part of the prefix; caches are per model anyway).
- FOOD-55's `RECEIPT_OCR_FIRST` and `RECEIPT_ANALYSIS_CACHE` also default
  **off**. Routing does not flip them. Flag-off receipt upload still uses
  `analyze_receipt_image` on Opus (`opus_baseline`).
- Rollout order: (1) this PR — flag off, router logging live; (2) run the
  live evals for the tiers listed as `routed`; (3) enable in a canary
  environment and watch the model mix and `reason=escalated` rate for a
  few days; (4) enable everywhere; (5) only then consider moving
  `receipt.analyze_image` / `receipt.nutrition_estimate`, with their own
  fixtures.
- `forced_model()` in the router exists for the eval harness
  (`FOOD_EVAL_MODEL`). Do not use it in production code.

## Logging

One line per decision from `app.services.model_router`:

```
model_route call_site=receipt.pantry_match workflow=ambiguous_ingredient tier=sonnet model=claude-sonnet-5 reason=policy routing_enabled=True escalated=False confidence=
```

and the existing usage line from `app.services.anthropic_cache` now carries
`model=` as well:

```
anthropic call_site=receipt.pantry_match model=claude-sonnet-5 input_tokens=143 output_tokens=41 cache_creation_input_tokens=0 cache_read_input_tokens=0
```

`reason` is one of `default` (flag off), `policy` (flag on, routed tier),
`escalated` (flag on, low-confidence re-run), `forced` (eval harness). Model
mix per call site and escalation rate are both `count by (call_site, model,
reason)` over these lines; FOOD-54 owns the dashboard.

## Adding a call site

1. Add a `RoutePolicy` row to `POLICY` in `model_router.py` with a real
   rationale. Pick `default` = the tier the site would have used today.
2. Add the row to the table above.
3. Call `route_model("<call_site>").model` (and `escalation_for()` if the
   answer exposes a confidence signal) — never import a model constant into
   a service.
4. Add fixtures to `tests/evals/` if the site affects one of the QA bars, and
   run the live eval on the `routed` tier before setting it below `default`.
