"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, errorDetailFromBody, readJsonResponse } from "@/lib/api";
import { validateQuantity } from "@/lib/validation";
import { IngredientCard, type Ingredient } from "@/components/ingredient-card";
import { UnitSelect } from "@/components/unit-select";

export type DraftIngredient = {
  clientKey: string;
  store_item_name: string;
  ingredient_name: string;
  quantity: string;
  unit: string;
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

type DraftItemInput = {
  store_item_name?: string;
  ingredient_name: string;
  quantity?: string | null;
  unit?: string | null;
  serving_size?: string | null;
  servings_per_container?: number | null;
  calories?: number | null;
  protein_g?: number | null;
  carbs_g?: number | null;
  fat_g?: number | null;
  fiber_g?: number | null;
  sodium_mg?: number | null;
  nutrition_notes?: string | null;
  is_manual?: boolean;
  is_food?: boolean;
};

function parseQuantity(value: string): number | null {
  const cleaned = value.trim();
  if (!cleaned) {
    return null;
  }

  if (cleaned.includes("/")) {
    const [numerator, denominator] = cleaned.split("/", 2).map(Number);
    if (
      Number.isFinite(numerator) &&
      Number.isFinite(denominator) &&
      denominator !== 0
    ) {
      return numerator / denominator;
    }
    return null;
  }

  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatQuantity(value: number): string {
  const rounded = Math.round(value * 10000) / 10000;
  if (Math.abs(rounded - Math.round(rounded)) < 1e-6) {
    return String(Math.round(rounded));
  }
  return String(rounded);
}

function sumQuantities(left: string, right: string): string {
  const leftValue = parseQuantity(left);
  const rightValue = parseQuantity(right);

  if (leftValue == null && rightValue == null) {
    return left || right;
  }

  return formatQuantity((leftValue ?? 0) + (rightValue ?? 0));
}

function mergeDraftItems(items: DraftItemInput[]): DraftItemInput[] {
  const merged = new Map<string, DraftItemInput>();
  const order: string[] = [];

  for (const item of items) {
    if (item.is_food === false) {
      continue;
    }

    const name = item.ingredient_name.trim();
    if (!name) {
      continue;
    }

    const unit = (item.unit ?? "").trim().toLowerCase();
    const key = `${name.toLowerCase()}::${unit}`;
    const existing = merged.get(key);

    if (!existing) {
      merged.set(key, { ...item, ingredient_name: name });
      order.push(key);
      continue;
    }

    existing.quantity = sumQuantities(
      existing.quantity ?? "",
      item.quantity ?? "",
    );
    existing.is_manual = Boolean(existing.is_manual) && Boolean(item.is_manual);

    if (!existing.serving_size && item.serving_size) {
      existing.serving_size = item.serving_size;
    }
    if (
      existing.servings_per_container == null &&
      item.servings_per_container != null
    ) {
      existing.servings_per_container = item.servings_per_container;
    }
    for (const field of [
      "calories",
      "protein_g",
      "carbs_g",
      "fat_g",
      "fiber_g",
      "sodium_mg",
      "nutrition_notes",
    ] as const) {
      if (existing[field] == null && item[field] != null) {
        existing[field] = item[field] as never;
      }
    }
  }

  return order.map((key) => merged.get(key)!);
}

function draftFromApi(items: DraftItemInput[]): DraftIngredient[] {
  return mergeDraftItems(items)
    .filter((item) => item.is_food !== false)
    .map((item) => ({
      clientKey: crypto.randomUUID(),
      store_item_name: item.store_item_name ?? "",
      ingredient_name: item.ingredient_name,
      quantity: item.quantity ?? "",
      unit: item.unit ?? "",
      serving_size: item.serving_size ?? null,
      servings_per_container: item.servings_per_container ?? null,
      calories: item.calories ?? null,
      protein_g: item.protein_g ?? null,
      carbs_g: item.carbs_g ?? null,
      fat_g: item.fat_g ?? null,
      fiber_g: item.fiber_g ?? null,
      sodium_mg: item.sodium_mg ?? null,
      nutrition_notes: item.nutrition_notes ?? null,
      is_manual: item.is_manual ?? false,
    }));
}

function toPayloadItem(item: DraftIngredient) {
  return {
    store_item_name: item.store_item_name || item.ingredient_name,
    ingredient_name: item.ingredient_name,
    quantity: item.quantity || null,
    unit: item.unit || null,
    serving_size: item.serving_size,
    servings_per_container: item.servings_per_container,
    calories: item.calories,
    protein_g: item.protein_g,
    carbs_g: item.carbs_g,
    fat_g: item.fat_g,
    fiber_g: item.fiber_g,
    sodium_mg: item.sodium_mg,
    nutrition_notes: item.nutrition_notes,
    is_manual: item.is_manual,
    is_food: true,
  };
}

function draftToPreview(item: DraftIngredient): Ingredient {
  return {
    id: item.clientKey,
    name: item.ingredient_name,
    store_item_name: item.store_item_name || null,
    quantity: item.quantity || null,
    unit: item.unit || null,
    serving_size: item.serving_size,
    servings_per_container: item.servings_per_container,
    calories: item.calories,
    protein_g: item.protein_g,
    carbs_g: item.carbs_g,
    fat_g: item.fat_g,
    fiber_g: item.fiber_g,
    sodium_mg: item.sodium_mg,
    nutrition_notes: item.is_manual
      ? "Nutrition will be estimated when you save."
      : item.nutrition_notes,
    receipt_id: null,
    created_at: new Date().toISOString(),
  };
}

export function ReceiptReview({
  receiptId,
  storeName,
  initialItems,
  onDraftChange,
  onConfirmed,
  onCancel,
}: {
  receiptId: string;
  storeName: string | null;
  initialItems: DraftItemInput[];
  onDraftChange?: (items: DraftItemInput[]) => void;
  onConfirmed: (ingredients: Ingredient[]) => void;
  onCancel: () => void;
}) {
  const [items, setItems] = useState(() => draftFromApi(initialItems));
  const [newName, setNewName] = useState("");
  const [newQuantity, setNewQuantity] = useState("");
  const [newUnit, setNewUnit] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const skipNextPersist = useRef(true);
  const onDraftChangeRef = useRef(onDraftChange);

  useEffect(() => {
    onDraftChangeRef.current = onDraftChange;
  }, [onDraftChange]);

  useEffect(() => {
    if (skipNextPersist.current) {
      skipNextPersist.current = false;
      return;
    }

    const payloadItems = items.map((item) => toPayloadItem(item));
    onDraftChangeRef.current?.(payloadItems);

    const timeoutId = window.setTimeout(async () => {
      try {
        await apiFetch(`/api/receipts/${receiptId}/draft`, {
          method: "PATCH",
          body: JSON.stringify({ items: payloadItems }),
        });
      } catch {
        // Keep local edits even if the draft sync request fails.
      }
    }, 300);

    return () => window.clearTimeout(timeoutId);
  }, [items, receiptId]);

  function updateItem(clientKey: string, updates: Partial<DraftIngredient>) {
    setItems((current) =>
      current.map((item) =>
        item.clientKey === clientKey ? { ...item, ...updates } : item,
      ),
    );
  }

  function removeItem(clientKey: string) {
    setItems((current) => current.filter((item) => item.clientKey !== clientKey));
  }

  function addItem() {
    const name = newName.trim();
    if (!name) {
      setError("Enter an ingredient name to add.");
      return;
    }

    const quantityError = validateQuantity(
      newQuantity.trim() || null,
      newUnit.trim() || null,
    );
    if (quantityError) {
      setError(quantityError);
      return;
    }

    setItems((current) => {
      const existingIndex = current.findIndex(
        (item) =>
          item.ingredient_name.trim().toLowerCase() === name.toLowerCase() &&
          item.unit.trim().toLowerCase() === newUnit.trim().toLowerCase(),
      );

      if (existingIndex === -1) {
        return [
          ...current,
          {
            clientKey: crypto.randomUUID(),
            store_item_name: name,
            ingredient_name: name,
            quantity: newQuantity.trim() || "1",
            unit: newUnit.trim() || "each",
            serving_size: null,
            servings_per_container: null,
            calories: null,
            protein_g: null,
            carbs_g: null,
            fat_g: null,
            fiber_g: null,
            sodium_mg: null,
            nutrition_notes: null,
            is_manual: true,
          },
        ];
      }

      return current.map((item, index) =>
        index === existingIndex
          ? {
              ...item,
              quantity: sumQuantities(item.quantity, newQuantity.trim()),
            }
          : item,
      );
    });

    setNewName("");
    setNewQuantity("");
    setNewUnit("");
    setError("");
  }

  async function handleConfirm() {
    const validItems = items.filter((item) => item.ingredient_name.trim());

    if (validItems.length === 0) {
      setError("Add at least one ingredient before saving.");
      return;
    }

    for (const item of validItems) {
      const quantityError = validateQuantity(
        item.quantity.trim() || null,
        item.unit.trim() || null,
      );
      if (quantityError) {
        setError(`"${item.ingredient_name.trim()}": ${quantityError}`);
        return;
      }
    }

    setSaving(true);
    setError("");

    try {
      const response = await apiFetch(`/api/receipts/${receiptId}/confirm`, {
        method: "POST",
        body: JSON.stringify({
          items: validItems.map((item) =>
            toPayloadItem({
              ...item,
              ingredient_name: item.ingredient_name.trim(),
              unit: item.unit.trim() || "each",
              quantity: item.quantity.trim() || "1",
            }),
          ),
        }),
      });

      const data = await readJsonResponse<{ ingredients: Ingredient[] }>(response);

      if (!response.ok) {
        throw new Error(errorDetailFromBody(data, "Unable to save ingredients."));
      }

      onConfirmed(data.ingredients);
    } catch (confirmError) {
      setError(
        confirmError instanceof Error
          ? confirmError.message
          : "Unable to save ingredients.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6 rounded-2xl border border-orange-200 bg-orange-50/40 p-6">
      <div>
        <h3 className="text-lg font-semibold text-stone-900">
          Review ingredients
        </h3>
        <p className="mt-1 text-sm text-stone-600">
          Check what Claude found from{" "}
          <span className="font-medium">{storeName ?? "your receipt"}</span>.
          Add or remove items, then save. Nutrition for manually added items
          will be estimated automatically.
        </p>
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-stone-500">
          No ingredients left. Add items below before saving.
        </p>
      ) : (
        <ul className="space-y-4">
          {items.map((item) => {
            return (
              <li
                key={item.clientKey}
                className="rounded-2xl border border-stone-200 bg-white p-4"
              >
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="grid flex-1 gap-3 sm:grid-cols-2">
                    <label className="block text-sm">
                      <span className="mb-1 block font-medium text-stone-700">
                        Ingredient name
                      </span>
                      <input
                        value={item.ingredient_name}
                        onChange={(event) =>
                          updateItem(item.clientKey, {
                            ingredient_name: event.target.value,
                          })
                        }
                        className={inputClassName}
                      />
                    </label>
                    <label className="block text-sm">
                      <span className="mb-1 block font-medium text-stone-700">
                        Receipt label
                      </span>
                      <input
                        value={item.store_item_name}
                        onChange={(event) =>
                          updateItem(item.clientKey, {
                            store_item_name: event.target.value,
                          })
                        }
                        className={inputClassName}
                      />
                    </label>
                    <label className="block text-sm">
                      <span className="mb-1 block font-medium text-stone-700">
                        Quantity
                      </span>
                      <input
                        value={item.quantity}
                        onChange={(event) =>
                          updateItem(item.clientKey, {
                            quantity: event.target.value,
                          })
                        }
                        className={inputClassName}
                      />
                    </label>
                    <label className="block text-sm">
                      <span className="mb-1 block font-medium text-stone-700">
                        Unit
                      </span>
                      <UnitSelect
                        value={item.unit}
                        onChange={(nextUnit) =>
                          updateItem(item.clientKey, { unit: nextUnit })
                        }
                        required={false}
                        className={inputClassName}
                      />
                    </label>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeItem(item.clientKey)}
                    className="shrink-0 rounded-lg px-2 py-1 text-sm text-red-600 transition hover:bg-red-50"
                  >
                    Remove
                  </button>
                </div>

                {item.is_manual ? (
                  <p className="text-xs text-orange-700">
                    Manually added — nutrition will be estimated on save.
                  </p>
                ) : (
                  <IngredientCard ingredient={draftToPreview(item)} compact />
                )}
              </li>
            );
          })}
        </ul>
      )}

      <div className="rounded-2xl border border-dashed border-stone-300 bg-white p-4">
        <h4 className="text-sm font-semibold text-stone-800">Add an ingredient</h4>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <input
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder="Ingredient name"
            className={inputClassName}
          />
          <input
            value={newQuantity}
            onChange={(event) => setNewQuantity(event.target.value)}
            placeholder="Quantity"
            className={inputClassName}
          />
          <UnitSelect value={newUnit} onChange={setNewUnit} required={false} />
        </div>
        <button
          type="button"
          onClick={addItem}
          className="mt-3 rounded-xl border border-stone-200 px-4 py-2 text-sm font-medium text-stone-700 transition hover:bg-stone-50"
        >
          Add ingredient
        </button>
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={handleConfirm}
          disabled={saving}
          className="rounded-xl bg-orange-500 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {saving ? "Saving & estimating nutrition..." : "Save ingredients"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded-xl border border-stone-200 px-5 py-2.5 text-sm font-medium text-stone-700 transition hover:bg-stone-50"
        >
          Cancel review
        </button>
      </div>
    </div>
  );
}

const inputClassName =
  "w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-900 outline-none transition focus:border-orange-400 focus:bg-white focus:ring-4 focus:ring-orange-100";
