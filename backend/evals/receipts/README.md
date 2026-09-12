# Receipt extraction eval set (FOOD-55)

Labeled receipts used to decide whether the OCR-first pipeline
(`RECEIPT_OCR_FIRST=1`) may replace the vision-Opus baseline as the default.
Food QA owns the pass/fail call; this folder gives them a fixed set, a label
format, and a runner that prints the numbers.

**Status: scaffold.** Every case is `"status": "placeholder"` — labels,
manifest entries and OCR-text fixtures exist, real images do not yet. Filling
the images is not a blocker for merging slices 1–2 (flags default OFF); it is a
blocker for flipping the flag.

## Layout

```
evals/receipts/
  README.md              this file
  manifest.json          the case list Food QA gates on
  costs.json             $/call per path (placeholder numbers, see below)
  schema/label.schema.json
  scoring.py             metric definitions (pure functions, unit-tested)
  run_eval.py            validate / run / score / dump-ocr
  cases/<case_id>/
    labels.json          ground truth (schema above)
    receipt.jpg|pdf      the input — NOT committed yet, see the case README
    ocr_text.txt         Tesseract-shaped transcript so the parser can be scored today
    README.md            what the image should look like and why the case exists
```

## Label schema

`schema/label.schema.json` (JSON Schema 2020-12); `scoring.Label` is the
Python twin. Top level: `case_id`, `store_name`, `items[]`, optional `totals`.

Each item: `store_item_name` (as printed, minus UPCs), `ingredient_name`
(plain English, abbreviations expanded, store brand dropped), `quantity`,
`unit` (one of `app.validation.INGREDIENT_UNITS`), `is_food`.

Labeling rules:

- Every printed line item is labeled, food or not. `SUBTOTAL`/`TAX`/`TOTAL`/payment lines are not items.
- `quantity`/`unit` are `null` when the receipt does not print or imply them (see `missing_qty_unit_01`). Do not label the LLM's guess.
- Weight lines (`2.14 lb @ 0.69/lb`) belong to the item above/below; label `quantity: "2.14", unit: "lb"`.
- Size tokens in the name (`16 OZ`, `12 CT`, `1 GAL`) become `quantity`/`unit` (`ct` → `each`, `pk` → `pack`, `gal` → `gallon`).

## Categories (one placeholder case each today)

| category | case | what it stresses | expected path |
| --- | --- | --- | --- |
| clean_grocery | `clean_grocery_01` | happy path; name line + weight line | `ocr` |
| sku_abbrevs | `sku_abbrevs_01` | dense abbreviations, UPCs, tax flags | `ocr` |
| mixed_non_food | `mixed_non_food_01` | `is_food` precision, bag fee, coupon | `ocr` |
| blurry_thermal | `blurry_thermal_01` | low OCR confidence → gate → Haiku cleanup | `haiku` |
| multi_column | `multi_column_01` | price column split onto its own lines | `ocr` |
| missing_qty_unit | `missing_qty_unit_01` | produce with no qty/unit → gate → Haiku cleanup | `haiku` |
| reupload_hash | `reupload_hash_01` | byte-identical re-upload served from cache | `cache` |
| pdf | `pdf_01` | non-image input skips OCR and Haiku → Sonnet vision | `sonnet` |

Path names map to model constants in `app/config.py`: `haiku` =
`RECEIPT_OCR_CLEANUP_MODEL`, `sonnet` = `RECEIPT_OCR_VISION_FALLBACK_MODEL`,
`opus_baseline` = `RECEIPT_ANTHROPIC_MODEL` (flag-off behaviour and the
comparison baseline).

`smoke: true` cases are the zero-regression set. Add more cases by copying a
folder, appending a manifest entry, and running `validate`.

## Metrics (`scoring.py`)

Items are matched one-to-one: exact normalized `ingredient_name`, then exact
normalized `store_item_name`, then token Jaccard ≥ 0.5. Normalization is
lower-case, punctuation stripped, naive singularization.

| metric | definition |
| --- | --- |
| **item F1** (headline) | micro-averaged over all labeled items in the run; macro (per-case mean) also printed |
| exact-match rate | matched items whose `ingredient_name` tokens, `quantity` (numeric), `unit` and `is_food` all equal the label ÷ labeled items |
| is_food accuracy | matched items with the right `is_food` ÷ matched items |
| store accuracy | cases with normalized `store_name` equal to the label |
| **% receipts with no vision call** | successful parses whose path ∈ {`cache`, `ocr`, `haiku`} ÷ successful parses. A parse is successful when it yields ≥ 1 food item. |
| **$ / successful parse** | Σ `costs.json[path]` over all cases ÷ successful parses. `costs.json` holds placeholder per-call prices; Metrics eng replaces them with billed averages before gating. |

## Pass bar (`scoring.compare`)

1. Candidate item F1 ≥ baseline item F1 − 0.02 (`--f1-margin`).
2. No smoke regressions: every `smoke: true` case must succeed **and** land on its `expect_path`.
3. **Cheaper-but-worse is a FAIL.** Cost and %-no-vision are reported, never traded against F1.

`score --strict` (the merge gate) additionally fails when any case is
unscored (missing image, or escalated with no baseline result to fall back
to) or when no `--baseline` file is given. Without `--strict` the verdict is
advisory so the parser can be iterated on text fixtures today.

## Running

From `backend/`:

```bash
# 1. sanity-check manifest + labels (no images needed; runs in CI-free seconds)
python evals/receipts/run_eval.py validate

# 2. score the deterministic parser on the OCR-text fixtures (no Tesseract, no LLM)
python evals/receipts/run_eval.py run --mode ocr-text --out /tmp/cand.json
python evals/receipts/run_eval.py score --predictions /tmp/cand.json

# 3. once images exist: the full OCR-first ladder (Tesseract -> Haiku -> Sonnet; needs
#    ANTHROPIC_API_KEY for the LLM rungs) vs the vision-Opus baseline. This is the flip gate.
python evals/receipts/run_eval.py run --mode ladder   --out /tmp/cand.json
python evals/receipts/run_eval.py run --mode baseline --out /tmp/base.json
python evals/receipts/run_eval.py score --predictions /tmp/cand.json --baseline /tmp/base.json --strict

# OCR rung only (no LLM calls): escalations are recorded as `escalated` and scored
# with the --baseline result when present
python evals/receipts/run_eval.py run --mode ocr --out /tmp/ocr_only.json

# refresh ocr_text.txt fixtures from the real images
python evals/receipts/run_eval.py dump-ocr
```

Notes on the runner:

- `run` simulates the per-user content-hash cache: a byte-identical input seen earlier in the run is recorded as `path: cache` (disable with `--no-simulate-cache`).
- In `ocr-text` mode Tesseract confidence is unknown; the manifest's per-case `ocr_confidence` (else `--assume-confidence`, default 90) feeds the gate. `blurry_thermal_01` sets 38 so the low-confidence gate fires as it would on the real image.
- In `ocr-text` / `ocr` modes there are no LLM rungs, so gate failures are recorded as `path: escalated` and scored with the `--baseline` prediction for that case (charged at the baseline's cost). Those cases will then miss a `haiku`/`sonnet` `expect_path`; use `--mode ladder` for the real gate.
- `run` never touches the database; it calls `extract_ocr_first` / `extract_receipt_vision` directly and skips nutrition enrichment (out of scope for FOOD-55).

## Honest caveat about today's numbers

The current `ocr_text.txt` fixtures were hand-written against the same rules
the parser implements, so `item F1 = 1.0` on them proves the plumbing works,
not that the parser is accurate. The number that matters is `--mode ladder` on
real images against a `--mode baseline` run. Re-label from the real image
when you add one; the placeholder labels are only there so the schema and
manifest can be exercised.
