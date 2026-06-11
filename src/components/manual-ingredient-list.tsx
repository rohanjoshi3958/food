"use client";

import { useState } from "react";
import { apiFetch, parseError } from "@/lib/api";
import type { Ingredient } from "@/components/ingredient-card";
import { UnitSelect } from "@/components/unit-select";

const inputClassName =
  "w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-900 outline-none transition focus:border-orange-400 focus:bg-white focus:ring-4 focus:ring-orange-100";

export function ManualIngredientList({
  disabled = false,
  onIngredientAdded,
}: {
  disabled?: boolean;
  onIngredientAdded?: (ingredient: Ingredient) => void;
}) {
  const [name, setName] = useState("");
  const [quantity, setQuantity] = useState("");
  const [unit, setUnit] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  async function addItem() {
    const ingredientName = name.trim();
    if (!ingredientName) {
      setError("Enter an ingredient name.");
      return;
    }

    if (!unit) {
      setError("Select a unit.");
      return;
    }

    setSaving(true);
    setError("");
    setMessage("");

    try {
      const response = await apiFetch("/api/ingredients/manual", {
        method: "POST",
        body: JSON.stringify({
          ingredient_name: ingredientName,
          quantity: quantity.trim() || null,
          unit,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : "Unable to add ingredient.";
        throw new Error(detail);
      }

      setName("");
      setQuantity("");
      setUnit("");
      setMessage(`Added ${data.name} to your ingredients.`);
      onIngredientAdded?.(data);
    } catch (addError) {
      setError(
        addError instanceof Error
          ? addError.message
          : "Unable to add ingredient.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4 rounded-2xl border border-stone-200 bg-white p-5">
      <div>
        <h3 className="text-sm font-semibold text-stone-900">
          Add ingredients manually
        </h3>
        <p className="mt-1 text-sm text-stone-500">
          Ingredients are saved immediately and appear in View ingredients.
          You can also upload a receipt below to add more.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Ingredient name"
          disabled={disabled || saving}
          className={inputClassName}
        />
        <input
          value={quantity}
          onChange={(event) => setQuantity(event.target.value)}
          placeholder="Quantity"
          disabled={disabled || saving}
          className={inputClassName}
        />
        <UnitSelect
          value={unit}
          onChange={setUnit}
          disabled={disabled || saving}
        />
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {message && (
        <p className="text-sm text-green-700">{message}</p>
      )}

      <button
        type="button"
        onClick={addItem}
        disabled={disabled || saving}
        className="rounded-xl border border-stone-200 px-4 py-2 text-sm font-medium text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {saving ? "Adding..." : "Add ingredient"}
      </button>
    </div>
  );
}
