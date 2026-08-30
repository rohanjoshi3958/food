"use client";

import { useEffect, useMemo, useState } from "react";
import { IngredientNutritionDisplay } from "@/components/ingredient-nutrition-display";
import { useIngredientUnitWarning } from "@/hooks/use-ingredient-unit-warning";
import type { Ingredient } from "@/lib/ingredients";
import {
  formatServingCount,
  getIngredientStock,
  ingredientSummaryLine,
  servingsNoun,
} from "@/lib/ingredients";
import { apiFetch, parseError } from "@/lib/api";

function StockProgressBar({ ratio }: { ratio: number }) {
  const percent = Math.max(0, Math.min(100, ratio * 100));
  const barColor =
    percent <= 25 ? "bg-amber-500" : percent <= 50 ? "bg-orange-400" : "bg-emerald-500";

  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-stone-200">
      <div
        className={`h-full rounded-full transition-all ${barColor}`}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}

function IngredientGridCard({
  ingredient,
  selected,
  onSelect,
}: {
  ingredient: Ingredient;
  selected: boolean;
  onSelect: () => void;
}) {
  const stock = getIngredientStock(ingredient);
  const summary = ingredientSummaryLine(ingredient);

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-2xl border bg-white p-4 text-left transition hover:shadow-md ${
        stock.isLowStock && !selected
          ? "border-amber-300 hover:border-amber-400"
          : selected
            ? "border-orange-400 ring-2 ring-orange-200"
            : "border-stone-200 hover:border-stone-300"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="line-clamp-2 font-medium text-stone-900">{ingredient.name}</p>
        {stock.servingsLeft != null ? (
          <span
            className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
              stock.isLowStock
                ? "bg-amber-100 text-amber-800"
                : "bg-orange-100 text-orange-700"
            }`}
          >
            {formatServingCount(stock.servingsLeft)} left
          </span>
        ) : (
          stock.quantityLabel && (
            <span className="shrink-0 rounded-full bg-stone-100 px-2.5 py-0.5 text-xs font-medium text-stone-600">
              {stock.quantityLabel}
            </span>
          )
        )}
      </div>

      {stock.stockRatio != null && (
        <div className="mt-3">
          <StockProgressBar ratio={stock.stockRatio} />
        </div>
      )}

      {summary && (
        <p className="mt-2 line-clamp-2 text-xs text-stone-500">{summary}</p>
      )}
    </button>
  );
}

function IngredientDetail({
  ingredient,
  onClose,
  onRemove,
  removing,
}: {
  ingredient: Ingredient;
  onClose: () => void;
  onRemove: () => void;
  removing: boolean;
}) {
  const stock = getIngredientStock(ingredient);
  const persistedWarning = ingredient.unit_warning?.trim() || null;
  const { warning: liveWarning } = useIngredientUnitWarning(
    persistedWarning ? "" : ingredient.name,
    persistedWarning ? "" : ingredient.unit ?? "",
  );
  const unitWarning = persistedWarning || liveWarning;

  return (
    <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4 sm:p-6">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-stone-900">{ingredient.name}</h3>
          {ingredient.store_item_name &&
            ingredient.store_item_name !== ingredient.name && (
              <p className="mt-1 text-xs text-stone-500">
                Receipt: {ingredient.store_item_name}
              </p>
            )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-sm text-stone-600 transition hover:bg-stone-200/60"
          >
            Close
          </button>
          <button
            type="button"
            onClick={onRemove}
            disabled={removing}
            className="rounded-lg px-2 py-1 text-sm text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {removing ? "Removing..." : "Remove"}
          </button>
        </div>
      </div>

      {unitWarning && (
        <p className="mb-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
          {unitWarning}
        </p>
      )}

      {stock.servingsLeft != null && stock.originalServings != null && (
        <div className="mb-4 space-y-2">
          {stock.quantityLabel && (
            <p className="text-sm text-stone-600">
              On hand:{" "}
              <strong className="font-semibold text-stone-900">
                {stock.quantityLabel}
              </strong>
            </p>
          )}
          <p className="text-sm text-stone-600">
            ~{formatServingCount(stock.originalServings)}{" "}
            {servingsNoun(stock.originalServings)} ·{" "}
            <strong className="font-semibold text-stone-900">
              {formatServingCount(stock.servingsLeft)} left
            </strong>
          </p>
          {stock.stockRatio != null && <StockProgressBar ratio={stock.stockRatio} />}
        </div>
      )}

      {stock.servingsLeft == null && stock.quantityLabel && (
        <p className="mb-4 text-sm text-stone-600">
          On hand:{" "}
          <strong className="font-semibold text-stone-900">
            {stock.quantityLabel}
          </strong>
        </p>
      )}

      {ingredient.serving_size && (
        <p className="mb-4 text-sm text-stone-600">
          Standard serving: {ingredient.serving_size}
        </p>
      )}

      <IngredientNutritionDisplay ingredient={ingredient} />

      {ingredient.nutrition_notes && (
        <p className="mt-4 text-xs italic text-stone-400">
          {ingredient.nutrition_notes}
        </p>
      )}
    </div>
  );
}

function computePantrySummary(items: Ingredient[]) {
  let totalServings = 0;
  let hasServings = false;
  let runningLow = 0;

  for (const item of items) {
    const stock = getIngredientStock(item);
    if (stock.servingsLeft != null) {
      hasServings = true;
      totalServings += stock.servingsLeft;
    }
    if (stock.isLowStock) {
      runningLow += 1;
    }
  }

  return {
    itemCount: items.length,
    runningLow,
    totalServings: hasServings ? totalServings : null,
  };
}

export function IngredientsTab({ refreshKey }: { refreshKey: number }) {
  const [items, setItems] = useState<Ingredient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);

  useEffect(() => {
    async function loadIngredients() {
      setLoading(true);
      setError("");

      try {
        const response = await apiFetch("/api/ingredients");
        if (!response.ok) {
          throw new Error(await parseError(response, "Unable to load ingredients."));
        }

        setItems(await response.json());
      } catch (loadError) {
        setError(
          loadError instanceof Error
            ? loadError.message
            : "Unable to load ingredients.",
        );
      } finally {
        setLoading(false);
      }
    }

    loadIngredients();
  }, [refreshKey]);

  const filteredItems = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) {
      return items;
    }

    return items.filter((item) => {
      const haystack = [
        item.name,
        item.store_item_name,
        item.serving_size,
        item.unit,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return haystack.includes(query);
    });
  }, [items, search]);

  const summary = useMemo(() => computePantrySummary(items), [items]);

  async function removeIngredient(id: string) {
    setRemovingId(id);
    setError("");

    try {
      const response = await apiFetch(`/api/ingredients/${id}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        throw new Error(await parseError(response, "Unable to remove ingredient."));
      }

      setItems((current) => current.filter((item) => item.id !== id));
      if (selectedId === id) {
        setSelectedId(null);
      }
    } catch (removeError) {
      setError(
        removeError instanceof Error
          ? removeError.message
          : "Unable to remove ingredient.",
      );
    } finally {
      setRemovingId(null);
    }
  }

  function handleSelect(id: string) {
    setSelectedId((current) => (current === id ? null : id));
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">Your ingredients</h2>
        <p className="mt-1 text-sm text-stone-600">What&apos;s in your kitchen</p>
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {!loading && items.length > 0 && (
        <>
          <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-600">
            <span className="font-medium text-stone-800">
              {summary.itemCount} item{summary.itemCount === 1 ? "" : "s"}
            </span>
            {summary.runningLow > 0 && (
              <>
                {" · "}
                <span className="font-medium text-amber-700">
                  {summary.runningLow} running low
                </span>
              </>
            )}
            {summary.totalServings != null && (
              <>
                {" · "}
                <span>
                  ~{formatServingCount(summary.totalServings)} servings total
                </span>
              </>
            )}
          </div>

          <label className="block">
            <span className="sr-only">Search ingredients</span>
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search ingredients..."
              className="w-full rounded-xl border border-stone-200 bg-white px-4 py-2.5 text-sm text-stone-900 outline-none transition placeholder:text-stone-400 focus:border-orange-300 focus:ring-2 focus:ring-orange-100"
            />
          </label>
        </>
      )}

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {[0, 1, 2, 3].map((key) => (
            <div
              key={key}
              className="rounded-2xl border border-stone-200 bg-white p-4"
            >
              <div className="h-4 w-2/3 animate-pulse rounded bg-stone-200" />
              <div className="mt-3 h-1.5 animate-pulse rounded-full bg-stone-100" />
              <div className="mt-3 h-3 w-full animate-pulse rounded bg-stone-100" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="text-sm text-stone-500">
          No ingredients yet. Add one manually or upload a receipt.
        </p>
      ) : filteredItems.length === 0 ? (
        <p className="text-sm text-stone-500">
          No ingredients match &ldquo;{search.trim()}&rdquo;.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {filteredItems.map((item) => (
            <div key={item.id} className="contents">
              <IngredientGridCard
                ingredient={item}
                selected={selectedId === item.id}
                onSelect={() => handleSelect(item.id)}
              />
              {selectedId === item.id && (
                <div className="col-span-full">
                  <IngredientDetail
                    ingredient={item}
                    onClose={() => setSelectedId(null)}
                    onRemove={() => removeIngredient(item.id)}
                    removing={removingId === item.id}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
