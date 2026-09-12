# Prompt caching on Claude call sites (FOOD-56)

Every Claude call in the backend goes through
`app/services/anthropic_cache.py::create_cached_message`. It sends the
byte-stable part of each prompt as a single `system` text block carrying
`cache_control: {"type": "ephemeral"}` (5-minute TTL) and everything
per-request in `messages`, after the breakpoint.

Anthropic hashes the request prefix in order `tools -> system -> messages`
up to the last block marked with `cache_control`. A cache hit requires that
prefix to match a recent request byte for byte. That gives one rule:

**Nothing before the breakpoint may vary between requests.**

## Call sites and where the breakpoint sits

| Call site (`call_site` log tag) | Model | Cached prefix (`system`) | After the breakpoint (`messages`) |
| --- | --- | --- | --- |
| `receipt.analyze_image` | `RECEIPT_ANTHROPIC_MODEL` | `RECEIPT_ANALYSIS_PROMPT` (extraction rules + JSON shape) | the receipt image / PDF block |
| `receipt.nutrition_estimate` | `RECEIPT_ANTHROPIC_MODEL` | `NUTRITION_ESTIMATE_PROMPT` | `- Item / - Quantity purchased / - Unit` lines |
| `receipt.unit_check` | `RECEIPT_ANTHROPIC_MODEL` | `UNIT_CHECK_PROMPT` | `- Item / - Unit` lines |
| `receipt.pantry_match` | `RECEIPT_ANTHROPIC_MODEL` | `PANTRY_MATCH_PROMPT` (matching rules + JSON shape) | incoming item + pantry JSON snapshot |
| `meal.generate` | `MEAL_ANTHROPIC_MODEL` | `MEAL_GENERATION_PROMPT` rendered with `MEAL_CALORIE_MIN/MAX` (chef rules + calorie band + JSON schema) | `Available ingredients:` listing, previous-meal assistant turn, `FOLLOW_UP_PROMPT`, calorie-retry and "too similar" retry turns |
| `meal.image_prompt` | `MEAL_ANTHROPIC_MODEL` | `PROMPT_SYSTEM` | meal name / description / ingredients |

The `*_USER_PROMPT` templates in `receipt_analyzer.py` and
`meal_generator.py` are the *only* strings that get `.format()`-ed with
request data. The `*_PROMPT` constants used as `system_prefix` must contain
no `{placeholders}` other than module constants (the meal calorie band).

## Never put these before the breakpoint

- Timestamps, dates, "today is ...", request ids, trace ids, user ids.
- Pantry / inventory snapshots (`pantry_json`, `Available ingredients`).
- Receipt images or PDFs, or anything derived from the uploaded bytes.
- Per-item fields: ingredient names, quantities, units, store names.
- Meal fields: name, description, `ingredients_used`.
- Conversation history: previous assistant JSON, follow-up prompts, calorie
  retry nudges, "too similar" nudges.
- Anything read from `settings` at request time, feature flags, or A/B
  variants. If a prompt must vary by variant, give each variant its own
  stable prefix rather than interpolating a flag value.
- Unsorted `json.dumps()` output, `set` iteration, or dict ordering that
  depends on runtime state (these are silent invalidators).

## Things that *do* bust the cache (and that is fine, but be aware)

Because the prefix is hashed byte for byte, any of the following creates a
new cache entry for every caller and pays the 1.25x cache-write price on
the first request of each 5-minute window until the old entry expires:

- Editing rule wording, whitespace, or ordering in any `*_PROMPT` constant.
- Changing the JSON response shape / schema examples embedded in a prompt.
- Changing `MEAL_CALORIE_MIN` / `MEAL_CALORIE_MAX`.
- Changing the model id (caches are per model).
- Adding `tools`, or changing `system` block count/order.

Deploying a prompt change is expected to cause a burst of cache writes; it
is not a bug. What *is* a bug is a prefix that changes between requests
within a deploy — the usage log below will show `cache_read_input_tokens=0`
on every call.

## Minimum cacheable length

Anthropic silently skips caching when the prefix is shorter than the
model's minimum (currently 512 tokens for Claude Opus 5, 1,024 for Claude
Sonnet 5; check the
[prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
for the current table). No error is returned; both cache counters are just
`0`.

Current prefix sizes versus those minimums (rough, ~4 chars/token):

| Prefix | Model minimum | Status today |
| --- | --- | --- |
| `UNIT_CHECK_PROMPT` | 512 (Opus 5) | well below — will not cache |
| `PANTRY_MATCH_PROMPT` | 512 (Opus 5) | below — will not cache |
| `NUTRITION_ESTIMATE_PROMPT` | 512 (Opus 5) | likely below — will not cache |
| `PROMPT_SYSTEM` (meal image) | 1,024 (Sonnet 5) | well below — will not cache |
| `RECEIPT_ANALYSIS_PROMPT` | 512 (Opus 5) | near the line — verify in logs |
| `MEAL_GENERATION_PROMPT` (rendered) | 1,024 (Sonnet 5) | below — verify in logs |

**Expect near-zero `cache_read_input_tokens` across the board until these
prefixes grow past their model's minimum or Anthropic lowers the
minimums.** That is the expected state of this spike, not a regression.
**Do not pad prompts to reach the minimum** — that changes prompt
semantics, which this spike explicitly avoids. The breakpoints are already
in the right place and start paying off the moment a prefix crosses the
threshold.

## Verifying hits

`create_cached_message` logs one INFO line per call from the
`app.services.anthropic_cache` logger (`app/main.py` calls
`logging.basicConfig(level=logging.INFO)` so these lines are emitted under
uvicorn's default logging, which otherwise only configures its own
loggers):

```
anthropic call_site=receipt.pantry_match input_tokens=143 output_tokens=41 cache_creation_input_tokens=0 cache_read_input_tokens=612
```

- `cache_creation_input_tokens > 0`: prefix was written to the cache.
- `cache_read_input_tokens > 0`: prefix was served from cache.
- both `0` on repeated calls: prefix is below the minimum length, or
  something volatile sits before the breakpoint.

### Concurrent bursts write the cache more than once

`_enrich_receipt_nutrition` fans out `estimate_ingredient_nutrition` over a
`ThreadPoolExecutor` (up to 6 workers). Those parallel requests all start
against a cold cache, so the *first wave* of a receipt scan will show
`cache_creation_input_tokens > 0` on every in-flight call rather than one
write followed by reads — a cache entry only becomes readable once its
first response has started. Later waves within the 5-minute TTL (remaining
items on the same receipt, the confirm-step unit-check / nutrition /
pantry-match calls, the next receipt) are where reads should appear. When
judging hit rate, count reads across the whole receipt flow, not the first
`N` parallel calls. Serialising the burst to force a single write is a
latency trade-off and is out of scope here.

This logging is intentionally tiny. Cost / hit-rate dashboards are FOOD-54
and should wrap `create_cached_message` rather than the individual call
sites.

## Scope guardrails

- Interactive paths (receipt scan, per-item enrichment, meal generation,
  image prompt) stay on the synchronous Messages API. Do not move them to
  the Batch API for cost reasons; caching is the cost lever here.
- `tests/test_prompt_caching.py` asserts breakpoint placement for every
  call site and fails if a module calls `client.messages.create` directly.
