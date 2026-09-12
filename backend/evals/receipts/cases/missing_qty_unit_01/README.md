# missing_qty_unit_01 (placeholder)

Needs `receipt.jpg`: a farm-stand or bodega receipt listing produce by name
and price only. Every food line lacks quantity and unit, so
`missing_qty_unit_ratio` is 1.0 and the gate must escalate to the Haiku
cleanup rung (`expect_path: haiku`), which is allowed to guess sensible
units. Labels keep `quantity`/`unit` as `null`; scoring treats a predicted
default (`"1"`, `"each"`) as a mismatch on the exact-match metric but not on
item F1.
