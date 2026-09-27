# pdf_01 (placeholder)

Needs `receipt.pdf`: an emailed/online-order receipt (Instacart, Amazon
Fresh, store e-receipt). The OCR rung only handles raster images and there is
no OCR text for the Haiku rung, so PDFs must flow straight to Sonnet vision
with a `document` block (`expect_path: sonnet`). There is no `ocr_text.txt`
for this case by design.
