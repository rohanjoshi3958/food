"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { IngredientCard, type Ingredient } from "@/components/ingredient-card";
import { UnitSelect } from "@/components/unit-select";

export type DraftIngredient = {
  clientKey: string;
  store_item_name: string;
  ingredient_name: string;
  quantity: string;
  unit: string;
  serving_size: string | null;
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
  calories?: number | null;
  protein_g?: number | null;
  carbs_g?: number | null;
  fat_g?: number | null;
  fiber_g?: number | null;
  sodium_mg?: number | null;
  nutrition_notes?: string | null;
  is_manual?: boolean;
};

function draftFromApi(items: DraftItemInput[]): DraftIngredient[] {
  return items.map((item) => ({
    clientKey: crypto.randomUUID(),
    store_item_name: item.store_item_name ?? "",
    ingredient_name: item.ingredient_name,
    quantity: item.quantity ?? "",
    unit: item.unit ?? "",
    serving_size: item.serving_size ?? null,
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
    calories: item.calories,
    protein_g: item.protein_g,
    carbs_g: item.carbs_g,
    fat_g: item.fat_g,
    fiber_g: item.fiber_g,
    sodium_mg: item.sodium_mg,
    nutrition_notes: item.nutrition_notes,
    is_manual: item.is_manual,
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
  onConfirmed,
  onCancel,
}: {
  receiptId: string;
  storeName: string | null;
  initialItems: DraftItemInput[];
  onConfirmed: (ingredients: Ingredient[]) => void;
  onCancel: () => void;
}) {
  const [items, setItems] = useState(() => draftFromApi(initialItems));
  const [newName, setNewName] = useState("");
  const [newQuantity, setNewQuantity] = useState("");
  const [newUnit, setNewUnit] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

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

    if (!newUnit) {
      setError("Select a unit.");
      return;
    }

    setItems((current) => [
      ...current,
      {
        clientKey: crypto.randomUUID(),
        store_item_name: name,
        ingredient_name: name,
        quantity: newQuantity.trim(),
        unit: newUnit,
        serving_size: null,
        calories: null,
        protein_g: null,
        carbs_g: null,
        fat_g: null,
        fiber_g: null,
        sodium_mg: null,
        nutrition_notes: null,
        is_manual: true,
      },
    ]);

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

    if (validItems.some((item) => !item.unit)) {
      setError("Select a unit for every ingredient.");
      return;
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
            }),
          ),
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : "Unable to save ingredients.";
        throw new Error(detail);
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
        <h3 className="text-lg font-semibold text-stone-900">Review ingredients</h3>
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
          {items.map((item) => (
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
          ))}
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
          <UnitSelect value={newUnit} onChange={setNewUnit} />
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
