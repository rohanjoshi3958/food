#!/usr/bin/env python3
"""FOOD-55 receipt eval runner.

    python evals/receipts/run_eval.py validate
    python evals/receipts/run_eval.py run --mode ocr-text --out /tmp/cand.json
    python evals/receipts/run_eval.py run --mode ocr      --out /tmp/ocr.json    # needs images + Tesseract; no LLM
    python evals/receipts/run_eval.py run --mode ladder   --out /tmp/cand.json   # ocr -> haiku -> sonnet; needs ANTHROPIC_API_KEY
    python evals/receipts/run_eval.py run --mode baseline --out /tmp/base.json   # vision Opus; needs ANTHROPIC_API_KEY
    python evals/receipts/run_eval.py score --predictions /tmp/cand.json [--baseline /tmp/base.json]
    python evals/receipts/run_eval.py dump-ocr                                    # write Tesseract text next to images

Run from ``backend/``. Nothing here touches the database.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from evals.receipts.scoring import (  # noqa: E402
    ESCALATED,
    Label,
    Prediction,
    PredictionItem,
    RunScore,
    compare,
    score_run,
)


def load_manifest() -> dict:
    return json.loads((EVAL_DIR / "manifest.json").read_text())


def load_labels(manifest: dict) -> tuple[dict[str, Label], list[str]]:
    labels: dict[str, Label] = {}
    errors: list[str] = []
    for entry in manifest["cases"]:
        path = EVAL_DIR / entry["labels"]
        if not path.exists():
            errors.append(f"{entry['id']}: labels file missing ({entry['labels']})")
            continue
        try:
            label = Label.model_validate_json(path.read_text())
        except Exception as exc:  # pydantic ValidationError or bad JSON
            errors.append(f"{entry['id']}: labels invalid: {exc}")
            continue
        expected_case_id = Path(entry["labels"]).parent.name
        if label.case_id != expected_case_id:
            errors.append(f"{entry['id']}: labels.case_id {label.case_id!r} != folder {expected_case_id!r}")
        errors.extend(f"{entry['id']}: {problem}" for problem in label.problems())
        labels[entry["id"]] = label
    return labels, errors


def cmd_validate(_args) -> int:
    manifest = load_manifest()
    labels, errors = load_labels(manifest)
    seen: set[str] = set()
    for entry in manifest["cases"]:
        if entry["id"] in seen:
            errors.append(f"duplicate case id {entry['id']}")
        seen.add(entry["id"])
        if entry["category"] not in manifest["categories"]:
            errors.append(f"{entry['id']}: unknown category {entry['category']}")
        image = EVAL_DIR / entry["image"]
        if entry.get("status") == "ready" and not image.exists():
            errors.append(f"{entry['id']}: status=ready but image missing ({entry['image']})")
        if entry.get("ocr_text") and not (EVAL_DIR / entry["ocr_text"]).exists():
            errors.append(f"{entry['id']}: ocr_text missing ({entry['ocr_text']})")
    missing_categories = set(manifest["categories"]) - {e["category"] for e in manifest["cases"]}
    if missing_categories:
        errors.append(f"categories without cases: {sorted(missing_categories)}")

    ready = sum(1 for e in manifest["cases"] if e.get("status") == "ready")
    print(f"{len(manifest['cases'])} cases, {len(labels)} labels loaded, {ready} with real images")
    for error in errors:
        print(f"ERROR {error}")
    return 1 if errors else 0


def _prediction_from_parsed(parsed, path: str, **extra) -> Prediction:
    return Prediction(
        path=path,
        store_name=parsed.store_name,
        items=[PredictionItem(**item.model_dump(include={"store_item_name", "ingredient_name", "quantity", "unit", "is_food"})) for item in parsed.items],
        **extra,
    )


def _run_ocr_text(text: str, assumed_confidence: float) -> Prediction:
    from app.config import settings
    from app.services.receipt_gates import evaluate_ocr_gates
    from app.services.receipt_parser import parse_receipt_text

    started = time.perf_counter()
    outcome = parse_receipt_text(text)
    decision = evaluate_ocr_gates(
        outcome.receipt,
        outcome.diagnostics,
        assumed_confidence,
        min_ocr_confidence=settings.receipt_ocr_min_confidence,
        max_missing_qty_unit_ratio=settings.receipt_ocr_max_missing_qty_unit_ratio,
        totals_tolerance=settings.receipt_ocr_totals_tolerance,
    )
    return _prediction_from_parsed(
        outcome.receipt,
        "ocr" if decision.passed else ESCALATED,
        gate_passed=decision.passed,
        gate_reasons=decision.reasons,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def _run_ocr_image(image_bytes: bytes) -> Prediction:
    from app.services.receipt_ocr import run_tesseract
    from app.services.receipt_preprocess import prepare_for_ocr

    image = prepare_for_ocr(image_bytes)
    if image is None:
        return Prediction(path=ESCALATED, gate_reasons=["ocr_unavailable"], gate_passed=False)
    ocr = run_tesseract(image)
    prediction = _run_ocr_text(ocr.text, ocr.confidence or 0.0)
    return prediction


def _run_ladder(image_path: Path, image_bytes: bytes) -> Prediction:
    """The production OCR-first ladder (ocr -> haiku -> sonnet) minus nutrition enrichment."""
    from app.services.receipt_pipeline import extract_ocr_first

    outcome = extract_ocr_first(image_path, image_bytes)
    return _prediction_from_parsed(
        outcome.parsed,
        outcome.path,
        gate_passed=outcome.path == "ocr",
        gate_reasons=outcome.gate_reasons,
        latency_ms=outcome.latency_ms,
    )


def _run_baseline(image_path: Path, image_bytes: bytes) -> Prediction:
    from app.config import RECEIPT_ANTHROPIC_MODEL
    from app.services.receipt_analyzer import _media_type_for_path, extract_receipt_vision

    started = time.perf_counter()
    media_type, _ = _media_type_for_path(image_path)
    parsed = extract_receipt_vision(image_bytes, media_type, model=RECEIPT_ANTHROPIC_MODEL)
    return _prediction_from_parsed(parsed, "opus_baseline", latency_ms=(time.perf_counter() - started) * 1000)


def cmd_run(args) -> int:
    from app.services.receipt_preprocess import content_hash

    manifest = load_manifest()
    predictions: dict[str, dict] = {}
    # Mirrors the per-user content-hash cache: a byte-identical input seen
    # earlier in this run is served from the first result at zero cost.
    seen_hashes: dict[str, Prediction] = {}
    for entry in manifest["cases"]:
        case_id = entry["id"]
        image = EVAL_DIR / entry["image"]
        text_path = EVAL_DIR / entry["ocr_text"] if entry.get("ocr_text") else None
        try:
            if args.mode == "ocr-text":
                source = text_path
                if source is None or not source.exists():
                    print(f"skip {case_id}: no ocr_text fixture")
                    continue
            else:
                source = image
                if not source.exists():
                    print(f"skip {case_id}: image missing ({entry['image']})")
                    continue

            payload = source.read_bytes()
            digest = content_hash(payload)
            if args.simulate_cache and digest in seen_hashes:
                prediction = seen_hashes[digest].model_copy(update={"path": "cache", "latency_ms": 0.0})
            elif args.mode == "ocr-text":
                confidence = float(entry.get("ocr_confidence", args.assume_confidence))
                prediction = _run_ocr_text(payload.decode("utf-8"), confidence)
            elif args.mode == "ocr":
                prediction = _run_ocr_image(payload)
            elif args.mode == "ladder":
                prediction = _run_ladder(image, payload)
            else:
                prediction = _run_baseline(image, payload)
            seen_hashes.setdefault(digest, prediction)
        except Exception as exc:
            prediction = Prediction(path="error", error=f"{type(exc).__name__}: {exc}")
        predictions[case_id] = prediction.model_dump()
        print(f"{case_id:24s} path={prediction.path:14s} items={len(prediction.items):2d} reasons={prediction.gate_reasons}")

    Path(args.out).write_text(json.dumps(predictions, indent=2))
    print(f"wrote {len(predictions)} predictions to {args.out}")
    return 0


def _load_predictions(path: str | None) -> dict[str, Prediction] | None:
    if not path:
        return None
    raw = json.loads(Path(path).read_text())
    return {case_id: Prediction.model_validate(value) for case_id, value in raw.items()}


def _print_run(title: str, run: RunScore) -> None:
    print(f"\n== {title}")
    for case in run.cases:
        flag = "" if case.path_ok else f"  (expected {case.expect_path})"
        err = f"  [{case.error}]" if case.error else ""
        print(f"  {case.case_id:24s} path={case.path:14s} f1={case.f1:.2f} exact={case.exact}/{case.tp + case.fn} store={'Y' if case.store_match else 'N'}{flag}{err}")
    print(
        f"  item F1 (micro) {run.micro_f1:.3f} | macro {run.macro_f1:.3f} | exact-match {run.exact_match_rate:.3f} "
        f"| is_food acc {run.is_food_accuracy:.3f} | store acc {run.store_accuracy:.3f}"
    )
    print(f"  % no vision call {run.pct_no_vision:.1%} | $/successful parse {run.cost_per_success:.4f} | smoke failures {run.smoke_failures or 'none'}")
    for case_id, reason in run.skipped:
        print(f"  skipped {case_id}: {reason}")


def cmd_score(args) -> int:
    manifest = load_manifest()
    labels, errors = load_labels(manifest)
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1
    costs = {k: v for k, v in json.loads((EVAL_DIR / "costs.json").read_text()).items() if not k.startswith("_")}

    candidate_preds = _load_predictions(args.predictions) or {}
    baseline_preds = _load_predictions(args.baseline)

    candidate = score_run(candidate_preds, labels, manifest["cases"], costs, fallback=baseline_preds)
    _print_run("candidate", candidate)
    baseline = None
    if baseline_preds is not None:
        baseline = score_run(baseline_preds, labels, manifest["cases"], costs)
        _print_run("baseline (vision Opus)", baseline)

    verdict = compare(candidate, baseline, f1_margin=args.f1_margin, strict=args.strict)
    print(f"\nVERDICT: {verdict.status}{'' if args.strict else ' (advisory; pass --strict for the merge gate)'}")
    for reason in verdict.reasons:
        print(f"  - {reason}")
    if baseline is None and not args.strict:
        print("  (no --baseline given: F1 bar not enforced, only smoke/path checks)")
    return 0 if verdict.passed else 1


def cmd_dump_ocr(_args) -> int:
    from app.services.receipt_ocr import run_tesseract
    from app.services.receipt_preprocess import prepare_for_ocr

    manifest = load_manifest()
    for entry in manifest["cases"]:
        image = EVAL_DIR / entry["image"]
        if not image.exists() or not entry.get("ocr_text"):
            continue
        prepared = prepare_for_ocr(image.read_bytes())
        if prepared is None:
            continue
        result = run_tesseract(prepared)
        (EVAL_DIR / entry["ocr_text"]).write_text(result.text + "\n")
        print(f"{entry['id']}: {result.word_count} words, conf {result.confidence}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate").set_defaults(func=cmd_validate)

    run = sub.add_parser("run")
    run.add_argument("--mode", choices=["ocr-text", "ocr", "ladder", "baseline"], default="ocr-text")
    run.add_argument("--out", required=True)
    run.add_argument(
        "--assume-confidence",
        type=float,
        default=90.0,
        help="OCR confidence for ocr-text mode when the manifest entry has no ocr_confidence",
    )
    run.add_argument("--no-simulate-cache", dest="simulate_cache", action="store_false")
    run.set_defaults(func=cmd_run)

    score = sub.add_parser("score")
    score.add_argument("--predictions", required=True)
    score.add_argument("--baseline")
    score.add_argument("--f1-margin", type=float, default=0.02)
    score.add_argument("--strict", action="store_true", help="fail on skipped cases and missing baseline (merge gate)")
    score.set_defaults(func=cmd_score)

    sub.add_parser("dump-ocr").set_defaults(func=cmd_dump_ocr)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
