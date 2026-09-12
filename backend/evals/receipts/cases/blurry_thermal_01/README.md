# blurry_thermal_01 (placeholder)

Needs `receipt.jpg`: a faded thermal receipt with mild motion blur or a
crease through the price column. This is the negative case for the OCR path:
the confidence / totals gates should fire and the pipeline should escalate
(`expect_path: opus_baseline`, or `sonnet` when a vision fallback model is
configured). A pass here means the LLM rung produced the labeled items, not
that OCR did.
