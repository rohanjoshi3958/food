# blurry_thermal_01 (placeholder)

Needs `receipt.jpg`: a faded thermal receipt with mild motion blur or a
crease through the price column. This is the negative case for the OCR rung:
the confidence / totals gates should fire and the pipeline should escalate to
the Haiku OCR-text cleanup rung (`expect_path: haiku`). If Haiku also cannot
find food lines the case lands on Sonnet vision. A pass here means an LLM
rung produced the labeled items, not that the rules parser did.
