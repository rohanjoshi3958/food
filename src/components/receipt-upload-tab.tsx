"use client";

import { useEffect, useRef, useState } from "react";
import type { Ingredient } from "@/lib/ingredients";
import { ManualIngredientList } from "@/components/manual-ingredient-list";
import { ReceiptReview } from "@/components/receipt-review";
import {
  apiFetch,
  errorDetailFromBody,
  getToken,
  parseError,
  readJsonResponse,
} from "@/lib/api";
import { formatCookbookDateTime } from "@/lib/meals";

type DraftItem = {
  store_item_name: string;
  ingredient_name: string;
  quantity: string | null;
  unit: string | null;
  serving_size: string | null;
  servings_per_container: number | null;
  calories: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  sodium_mg: number | null;
  nutrition_notes: string | null;
  is_manual: boolean;
};

type Receipt = {
  id: string;
  original_name: string;
  store_name: string | null;
  analysis_status: string;
  analysis_error: string | null;
  uploaded_at: string;
  ingredients: Ingredient[];
  draft_items: DraftItem[];
};

function ReceiptStatusPill({ status }: { status: string }) {
  if (status === "completed") {
    return (
      <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
        Saved
      </span>
    );
  }
  if (status === "cancelled") {
    return (
      <span className="rounded-full bg-stone-200 px-2 py-0.5 text-xs font-medium text-stone-600">
        Cancelled
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
        Failed
      </span>
    );
  }

  return (
    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
      {status.replace(/_/g, " ")}
    </span>
  );
}

function StepIndicator({ step }: { step: 1 | 2 | 3 }) {
  const steps = [
    { id: 1, label: "Upload" },
    { id: 2, label: "Review" },
    { id: 3, label: "Save" },
  ] as const;

  return (
    <ol className="flex items-center gap-2 text-xs font-medium sm:gap-4">
      {steps.map((item, index) => {
        const isComplete = item.id < step;
        const isActive = item.id === step;

        return (
          <li key={item.id} className="flex items-center gap-2 sm:gap-4">
            <span
              className={`flex items-center gap-2 ${
                isActive
                  ? "text-orange-600"
                  : isComplete
                    ? "text-stone-700"
                    : "text-stone-400"
              }`}
            >
              <span
                className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] ${
                  isActive
                    ? "bg-orange-500 text-white"
                    : isComplete
                      ? "bg-stone-800 text-white"
                      : "bg-stone-200 text-stone-500"
                }`}
              >
                {isComplete ? "✓" : item.id}
              </span>
              {item.label}
            </span>
            {index < steps.length - 1 && (
              <span className="hidden h-px w-6 bg-stone-200 sm:block" aria-hidden="true" />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function ReceiptDropZone({
  file,
  previewUrl,
  disabled,
  onFileSelected,
}: {
  file: File | null;
  previewUrl: string | null;
  disabled: boolean;
  onFileSelected: (file: File | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function pickFile(next: File | null) {
    if (!next) {
      onFileSelected(null);
      return;
    }

    const isPdf = next.type === "application/pdf" || next.name.toLowerCase().endsWith(".pdf");
    const isImage = next.type.startsWith("image/");
    if (!isImage && !isPdf) {
      return;
    }

    onFileSelected(next);
  }

  function handleDrop(event: React.DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (disabled) {
      return;
    }

    pickFile(event.dataTransfer.files?.[0] ?? null);
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) {
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={`mt-4 rounded-2xl border-2 border-dashed p-6 text-center transition ${
        dragging
          ? "border-orange-400 bg-orange-50"
          : "border-stone-300 bg-orange-50/40"
      } ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:border-orange-300"}`}
      onClick={() => {
        if (!disabled) {
          inputRef.current?.click();
        }
      }}
      onKeyDown={(event) => {
        if (disabled) {
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          inputRef.current?.click();
        }
      }}
      role="button"
      tabIndex={disabled ? -1 : 0}
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/*,.pdf"
        className="hidden"
        disabled={disabled}
        onChange={(event) => pickFile(event.target.files?.[0] ?? null)}
      />

      {file && previewUrl ? (
        <div className="space-y-3">
          <img
            src={previewUrl}
            alt="Receipt preview"
            className="mx-auto max-h-40 rounded-xl object-contain ring-1 ring-stone-200"
          />
          <p className="text-sm font-medium text-stone-800">{file.name}</p>
        </div>
      ) : file ? (
        <div className="space-y-2">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-xl bg-white text-sm font-bold text-stone-500 ring-1 ring-stone-200">
            PDF
          </div>
          <p className="text-sm font-medium text-stone-800">{file.name}</p>
        </div>
      ) : (
        <>
          <p className="text-sm font-medium text-stone-800">
            Drop receipt here or click to browse
          </p>
          <p className="mt-1 text-xs text-stone-500">
            JPG, PNG, or PDF · Claude reads items for you to review
          </p>
        </>
      )}
    </div>
  );
}

function ReceiptHistory({
  receipts,
  loading,
}: {
  receipts: Receipt[];
  loading: boolean;
}) {
  const [open, setOpen] = useState(true);

  if (!loading && receipts.length === 0) {
    return null;
  }

  return (
    <div className="rounded-2xl border border-stone-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <span className="text-sm font-semibold text-stone-900">Receipt history</span>
        <span className="text-xs text-stone-500">
          {open ? "Hide" : "Show"} · {receipts.length}
        </span>
      </button>

      {open && (
        <ul className="divide-y divide-stone-100 border-t border-stone-100">
          {loading ? (
            <li className="px-4 py-3 text-sm text-stone-500">Loading...</li>
          ) : (
            receipts.map((receipt) => {
              const itemCount =
                receipt.draft_items.length > 0
                  ? receipt.draft_items.length
                  : receipt.ingredients.length;

              return (
                <li key={receipt.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-stone-800">
                        {receipt.store_name ?? receipt.original_name}
                      </p>
                      <p className="mt-0.5 text-xs text-stone-500">
                        {itemCount} item{itemCount === 1 ? "" : "s"} ·{" "}
                        {formatCookbookDateTime(receipt.uploaded_at)}
                      </p>
                      {receipt.analysis_status === "failed" &&
                        receipt.analysis_error && (
                          <p className="mt-1 text-xs text-red-600">
                            {receipt.analysis_error}
                          </p>
                        )}
                    </div>
                    <ReceiptStatusPill status={receipt.analysis_status} />
                  </div>
                </li>
              );
            })
          )}
        </ul>
      )}
    </div>
  );
}

export function ReceiptUploadTab({
  onIngredientsChanged,
  onViewIngredients,
}: {
  onIngredientsChanged: () => void;
  onViewIngredients: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [reviewReceipt, setReviewReceipt] = useState<Receipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showIngredientsLink, setShowIngredientsLink] = useState(false);
  const reviewReceiptRef = useRef<Receipt | null>(null);

  useEffect(() => {
    reviewReceiptRef.current = reviewReceipt;
  }, [reviewReceipt]);

  useEffect(() => {
    if (!file || !file.type.startsWith("image/")) {
      setPreviewUrl(null);
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  async function loadReceipts() {
    setLoading(true);
    setError("");

    try {
      const response = await apiFetch("/api/receipts");
      if (!response.ok) {
        throw new Error(await parseError(response, "Unable to load receipts."));
      }

      setReceipts(await response.json());
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load receipts.",
      );
    } finally {
      setLoading(false);
    }
  }

  function discardPendingReceiptsKeepalive() {
    const token = getToken();
    if (!token) {
      return;
    }

    void fetch("/api/receipts/discard-pending", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      keepalive: true,
    });
  }

  useEffect(() => {
    async function init() {
      try {
        await apiFetch("/api/receipts/discard-pending", { method: "POST" });
      } catch {
        // Still load the receipts list if cleanup fails.
      }
      await loadReceipts();
    }

    init();

    function handlePageHide() {
      if (reviewReceiptRef.current) {
        discardPendingReceiptsKeepalive();
      }
    }

    window.addEventListener("pagehide", handlePageHide);
    return () => {
      window.removeEventListener("pagehide", handlePageHide);
      if (reviewReceiptRef.current) {
        discardPendingReceiptsKeepalive();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleUpload() {
    if (!file) {
      setError("Choose a receipt photo to upload.");
      return;
    }

    setUploading(true);
    setError("");
    setMessage("");
    setShowIngredientsLink(false);
    setReviewReceipt(null);

    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("manual_items", "[]");

      const response = await apiFetch("/api/receipts/upload", {
        method: "POST",
        body: formData,
      });

      const data = await readJsonResponse<Receipt>(response);

      if (!response.ok) {
        throw new Error(errorDetailFromBody(data, "Upload failed."));
      }

      setFile(null);
      setReviewReceipt(data);
      setMessage(
        `Ready to review ${data.draft_items.length} item${data.draft_items.length === 1 ? "" : "s"} from ${data.store_name ?? "your receipt"}.`,
      );
      await loadReceipts();
    } catch (uploadError) {
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : "Upload failed.",
      );
    } finally {
      setUploading(false);
    }
  }

  function handleConfirmed(ingredients: Ingredient[]) {
    setReviewReceipt(null);
    setMessage(
      `Saved ${ingredients.length} ingredient${ingredients.length === 1 ? "" : "s"} to your kitchen.`,
    );
    setShowIngredientsLink(true);
    onIngredientsChanged();
    loadReceipts();
  }

  const currentStep: 1 | 2 | 3 = reviewReceipt ? 2 : uploading ? 1 : 1;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">Add to your kitchen</h2>
        <p className="mt-1 text-sm text-stone-600">
          Scan a receipt or add items by hand
        </p>
      </div>

      <StepIndicator step={showIngredientsLink ? 3 : currentStep} />

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {message && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-green-50 px-4 py-3 text-sm text-green-700">
          <span>{message}</span>
          {showIngredientsLink && (
            <button
              type="button"
              onClick={onViewIngredients}
              className="rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-green-800 ring-1 ring-green-200 transition hover:bg-green-100"
            >
              View ingredients
            </button>
          )}
        </div>
      )}

      {uploading && (
        <div className="rounded-2xl border border-orange-200 bg-orange-50 px-4 py-5">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 h-5 w-5 animate-spin rounded-full border-2 border-orange-200 border-t-orange-600" />
            <div>
              <p className="font-medium text-stone-900">Reading your receipt…</p>
              <p className="mt-1 text-sm text-stone-600">
                Claude is extracting items and nutrition. This can take up to a
                minute.
              </p>
            </div>
          </div>
        </div>
      )}

      {!reviewReceipt && !uploading && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-2xl border border-orange-200 bg-white p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-stone-900">Scan a receipt</h3>
            <p className="mt-1 text-sm text-stone-500">
              Upload a grocery receipt and review items before saving.
            </p>

            <ReceiptDropZone
              file={file}
              previewUrl={previewUrl}
              disabled={uploading}
              onFileSelected={setFile}
            />

            <button
              type="button"
              onClick={handleUpload}
              disabled={uploading || !file}
              className="mt-4 w-full rounded-xl bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Upload &amp; analyze receipt
            </button>
          </div>

          <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
            <ManualIngredientList
              embedded
              disabled={uploading}
              onIngredientAdded={() => {
                onIngredientsChanged();
                setMessage("Ingredient added to your kitchen.");
                setShowIngredientsLink(true);
              }}
            />
          </div>
        </div>
      )}

      {reviewReceipt && (
        <ReceiptReview
          key={reviewReceipt.id}
          receiptId={reviewReceipt.id}
          storeName={reviewReceipt.store_name}
          initialItems={reviewReceipt.draft_items}
          onDraftChange={(draftItems) => {
            setReviewReceipt((current) =>
              current
                ? { ...current, draft_items: draftItems as DraftItem[] }
                : null,
            );
            setReceipts((current) =>
              current.map((receipt) =>
                receipt.id === reviewReceipt.id
                  ? { ...receipt, draft_items: draftItems as DraftItem[] }
                  : receipt,
              ),
            );
          }}
          onConfirmed={handleConfirmed}
          onCancel={async () => {
            try {
              await apiFetch(`/api/receipts/${reviewReceipt.id}/cancel`, {
                method: "POST",
              });
            } catch {
              // Still close the review UI if cancel request fails.
            }
            setReviewReceipt(null);
            await loadReceipts();
          }}
        />
      )}

      <ReceiptHistory receipts={receipts} loading={loading} />
    </div>
  );
}
