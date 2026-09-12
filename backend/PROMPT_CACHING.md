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
`0`. Several prefixes here (`UNIT_CHECK_PROMPT`, `PROMPT_SYSTEM`) are well
below that and will not cache today. **Do not pad prompts to reach the
minimum** — that changes prompt semantics, which this spike explicitly
avoids. The breakpoints are still correct and become effective as soon as a
prompt grows past the threshold or Anthropic lowers it.

## Verifying hits

`create_cached_message` logs one INFO line per call from the
`app.services.anthropic_cache` logger:

```
anthropic call_site=receipt.pantry_match input_tokens=143 output_tokens=41 cache_creation_input_tokens=0 cache_read_input_tokens=612
```

- `cache_creation_input_tokens > 0`: prefix was written to the cache.
- `cache_read_input_tokens > 0`: prefix was served from cache.
- both `0` on repeated calls: prefix is below the minimum length, or
  something volatile sits before the breakpoint.

This logging is intentionally tiny. Cost / hit-rate dashboards are FOOD-54
and should wrap `create_cached_message` rather than the individual call
sites.

## Scope guardrails

- Interactive paths (receipt scan, per-item enrichment, meal generation,
  image prompt) stay on the synchronous Messages API. Do not move them to
  the Batch API for cost reasons; caching is the cost lever here.
- `tests/test_prompt_caching.py` asserts breakpoint placement for every
  call site and fails if a module calls `client.messages.create` directly.
