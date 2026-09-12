# pdf_01 (placeholder)

Needs `receipt.pdf`: an emailed/online-order receipt (Instacart, Amazon
Fresh, store e-receipt). The OCR rung only handles raster images, so PDFs
must still flow through the vision/document baseline (`expect_path:
opus_baseline`). There is no `ocr_text.txt` for this case by design.
