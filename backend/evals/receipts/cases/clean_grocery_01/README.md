# clean_grocery_01 (placeholder)

`receipt.jpg` is intentionally absent from the repo. Drop a sharp, well-lit
phone photo of a Whole Foods style receipt here (name on one line, weight and
price on the next) and set `"status": "ready"` in `manifest.json`.

`ocr_text.txt` is a hand-written Tesseract-shaped transcript so the parser can
be scored today with `run_eval.py run --mode ocr-text`. Replace it with real
Tesseract output (`run_eval.py dump-ocr`) once the image exists, then re-label.
