# Message Batches for offline Claude work (FOOD-57)

Anthropic Message Batches run the same Messages-API params asynchronously at
**50% of standard token prices**. Most batches finish within an hour (hard cap
24 hours). This is the cost lever for work that does not sit on a user click.

Prompt caching (FOOD-56) still applies. Interactive calls keep the default
5-minute cache TTL. Batch jobs send `cache_control.ttl = "1h"` because a
5-minute entry expires before a typical batch is processed.

## Interactive vs batch

| Path | Transport | Cache TTL | Entry point |
| --- | --- | --- | --- |
| Receipt upload / review / confirm | Sync `messages.create` | 5m (omit `ttl`) | `POST /api/receipts/*` → `receipt_analyzer` / `receipt_pipeline` |
| Meal generate ("try another") | Sync `messages.create` | 5m | `POST /api/meals/generate` → `generate_meal_from_ingredients` |
| Meal image prompt | Sync `messages.create` | 5m | `POST /api/meals/{id}/complete` → `meal_image` |
| Historical receipt reprocess | **Batch** `messages.batches` | **1h** | `python -m app.jobs.reprocess_receipts` |
| Nightly first-turn meal regen | **Batch** `messages.batches` | **1h** | `python -m app.jobs.regen_meals` |

Interactive routers and `app/services/{receipt_analyzer,meal_generator,meal_image}.py`
must not import `app.services.anthropic_batch` or `app.jobs`. Do not move a
user-facing call to Batch to save money — the user would wait minutes.

Shared construction:

- Sync: `create_cached_message` (no `cache_ttl`)
- Batch: `BatchMessageRequest.to_api_request` → `build_cached_message_params(..., cache_ttl="1h")`

Same `call_site` strings and `route_model()` decisions as the sync path
(`receipt.analyze_image`, `receipt.nutrition_estimate`, `meal.generate`).
`MODEL_ROUTING_ENABLED` stays off unless a later ticket flips it.

## Failure and retry

`run_message_batch` in `app/services/anthropic_batch.py`:

1. Validate unique `custom_id`s (`^[a-zA-Z0-9_-]{1,64}$`) and `max_tokens >= 1`.
2. Submit chunks (`client.messages.batches.create`).
3. Poll `retrieve` until `processing_status == "ended"` (`canceling` is not
   terminal). Default poll interval 30s, timeout 24h.
4. Stream `results` and map every row by `custom_id` (order is not stable).
5. Retry **one extra batch** (configurable `--max-retries`) for:
   - `expired` (24h window elapsed before the request ran)
   - `canceled`
   - `errored` whose `error_type` is **not** `invalid_request_error`,
     `authentication_error`, `billing_error`, `permission_error`, or
     `not_found_error`. Nested Anthropic envelopes
     (`{type: "error", error: {type, message}}`) are unwrapped first.
   - a `custom_id` missing from the results file
6. Never retry `succeeded` or a validation / auth / billing error — those
   need a code or config fix, not another submit.

A poll timeout raises `BatchTimeoutError` with the Anthropic batch id. The
batch keeps running on Anthropic's side; resume with
`poll_message_batch` + `collect_batch_results` using that id. Do not submit a
duplicate batch for the same items unless you intend to pay twice.

If a later chunk raises after earlier chunks succeeded,
`BatchRunInterrupted` carries the partial `outcome` (successes + submitted
ids). Jobs record that work, apply what they can, and exit non-zero.

Per-item failures in a mixed batch do not fail siblings. Jobs report
`vision_failed` / `nutrition_failed` / `failed` / `apply_failed` in the JSON
summary and leave successful rows eligible for `--apply`. The CLI exits `1`
when any of those collections is non-empty.

Unbilled Anthropic outcomes (`errored`, `canceled`, `expired`) are the ones
we retry. A succeeded parse that then fails local JSON validation is a
local error and is **not** automatically resubmitted.

## Operator jobs

From `backend/` with `ANTHROPIC_API_KEY` and `DATABASE_URL` set. Both jobs
are **dry-run** until `--apply`.

```bash
# Stored receipt files → vision batch, then per-item nutrition batch.
# --apply writes receipts.analysis_result only (not pantry, not draft_items).
python -m app.jobs.reprocess_receipts --receipt-id <uuid> --apply

# First-turn meal.generate per user pantry (no previous-meal follow-up,
# no calorie-retry loop). --apply replaces that user's meals row.
python -m app.jobs.regen_meals --user-id <uuid> --apply
```

`--poll-interval`, `--poll-timeout`, and `--max-retries` are on both CLIs.

Schedule with cron / systemd on staging first. There is no in-process
scheduler and no HTTP trigger.

## Cost shape

Batch input/output is half the sync list price. Stacked with a 1h cache hit,
shared prefixes (`RECEIPT_ANALYSIS_PROMPT`, `NUTRITION_ESTIMATE_PROMPT`,
`MEAL_GENERATION_PROMPT`) are charged at batch cache-read rates after the
first write in the hour. Cache writes at 1h TTL cost more than 5m writes;
that is still cheaper than letting the prefix miss on every request of a
long batch.

Do not enable `MODEL_ROUTING_ENABLED` or OCR-first flags from this spike.
