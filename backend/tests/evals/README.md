# Quality evals (FOOD-58)

Automated, fixture-driven quality gates for the three Food QA bars:

| Suite | QA bar | Entry point exercised | Fixtures |
| --- | --- | --- | --- |
| `test_receipt_parse_eval.py` | receipt accuracy | `analyze_receipt_image` (JSON extraction, schema, threaded nutrition enrichment) | `fixtures/receipts/*.json` |
| `test_ingredient_match_eval.py` | ingredient match rate | `create_ingredient` (unit check → nutrition → pantry match → merge/insert) and `canonicalize_draft_items` + `merge_draft_items` | `fixtures/pantry_matches.json` |
| `test_meal_plan_eval.py` | meal-plan acceptability | `generate_meal_from_ingredients` (parse → avoid repeat → clamp → scale → real calorie estimate → retry loop) | `fixtures/meal_plans.json` |
| `test_prompt_layout_eval.py` | FOOD-56 residual | prompt text frozen by hash; cached prefix + tail re-joins to the single-turn prompt | `fixtures/prompt_snapshots.json` |
| `test_live_evals.py` | all of the above, paid | same fixtures against the real API (opt-in) | — |

They run as part of `pytest` and as a dedicated CI step. No Anthropic calls
are made by default: `FakeClaude` (in `conftest.py`) routes each request on
its cached `system` prefix and answers from the fixture.

## How a fixture works

Each fixture pairs **what the model returned** with **what a reviewer
labelled as correct**, and the harness runs the real pipeline in between.
The labelled side includes deliberate model imperfections (an unexpanded
abbreviation, a mislabelled non-food line, a hallucinated pantry id) so the
metrics sit below 1.0 and thresholds have meaning. Two kinds of regression
fail the suite:

1. **Pipeline regressions** — someone weakens a guard (e.g. stops rejecting
   pantry ids the model invented) or breaks JSON extraction, portion
   scaling, or the calorie retry loop.
2. **Model-tier regressions** — the fixtures' `model_response` blocks are
   re-recorded from a cheaper tier and the labelled accuracy drops.

Calories in the meal suite are never mocked: acceptability uses
`calculate_meal_macros` on the pantry's label nutrition, so the 500–800 kcal
band is enforced by the same code production uses.

## Thresholds

`baselines.json` holds, per suite and metric, the documented `baseline`
(what the current corpus scores) and the CI gate (`min` or `max`). Gates are
set one fixture-level miss below the baseline on purpose. When a change
legitimately moves a number (new prompt wording, new model tier, corpus
growth), update `baseline` and, if warranted, the gate **in the same PR**
and explain why in the description.

At the end of a run pytest prints a summary table; in GitHub Actions the
same table is appended to the job's step summary.

## Running

```bash
cd backend
pytest tests/evals                 # mocked, ~5 s, what CI runs
pytest tests/evals -k receipt      # one suite
```

### Live (paid) — before changing model routing

```bash
FOOD_EVAL_LIVE=1 FOOD_EVAL_MODEL=claude-haiku-4-5 pytest tests/evals -m live -s
```

* `FOOD_EVAL_LIVE=1` enables the `live` marker; without it those tests skip.
* `FOOD_EVAL_MODEL` forces every call site onto one model id so a tier can be
  scored before it is routed. Leave unset to score the configured tiers.
* Receipt fixtures only run live when an image exists at
  `fixtures/receipts/images/<id><file_suffix>`; the repo ships labels only.
* `test_live_layout_ab_pantry_match` sends each pantry-match case both as the
  FOOD-56 cached layout and as a legacy single user turn and reports how often
  the decisions agree (`prompt_layout.layout_ab_agreement`).

Live metrics are recorded under `suite [live:<model>]` in the summary and are
gated by the same thresholds as the mocked run.

## Adding fixtures

* **Receipt**: add `fixtures/receipts/NN_name.json` with `model_response`
  (`payload` + optional `wrapper`: `plain` | `fenced` | `prose`),
  `nutrition_responses` keyed by the model's `ingredient_name`, and
  `expected.items` keyed by the printed `store_item_name`. Set `quantity` /
  `unit` to `null` when the receipt does not print them (they are then not
  scored) and `expect_recognized: false` for lines nutrition cannot identify.
* **Pantry match**: append to `cases` (one incoming item, seeded pantry,
  scripted response, expected `merge` / `new` / `ambiguous`) or to
  `draft_collapse`. Flag `hallucinated_id` / `ambiguous_flagged` so the
  guard metrics count the case.
* **Meal plan**: append to `cases` with ordered `model_responses` (the last
  one repeats if the retry loop keeps asking) and the labelled `acceptable`,
  `calls`, and `calories` the pipeline should reach.

Then run the suite, read the new numbers off the summary table, and update
`baselines.json`.
