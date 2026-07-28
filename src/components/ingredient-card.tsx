export type Ingredient = {
  id: string;
  name: string;
  store_item_name: string | null;
  quantity: string | null;
  original_quantity?: string | null;
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
  receipt_id: string | null;
  created_at: string;
};

function formatServingCount(value: number): string {
  if (!Number.isFinite(value)) {
    return String(value);
  }
  const rounded =
    Math.abs(value - Math.round(value)) < 0.05 ? Math.round(value) : value;
  if (typeof rounded === "number" && !Number.isInteger(rounded)) {
    return rounded.toFixed(1).replace(/\.0$/, "");
  }
  return String(rounded);
}

function servingsNoun(value: number): string {
  const rounded =
    Math.abs(value - Math.round(value)) < 0.05 ? Math.round(value) : value;
  return rounded === 1 ? "serving" : "servings";
}

function servingsFromQuantity(
  quantity: string | null | undefined,
  servingsPerContainer: number | null,
): number | null {
  if (
    servingsPerContainer == null ||
    servingsPerContainer <= 0 ||
    !quantity
  ) {
    return null;
  }

  const parsed = Number.parseFloat(quantity);
  if (!Number.isFinite(parsed)) {
    return null;
  }

  return parsed * servingsPerContainer;
}

export function IngredientCard({
  ingredient,
  compact = false,
  onRemove,
  removing = false,
}: {
  ingredient: Ingredient;
  compact?: boolean;
  onRemove?: () => void;
  removing?: boolean;
}) {
  const servingsLeft = servingsFromQuantity(
    ingredient.quantity,
    ingredient.servings_per_container,
  );
  const originalServings = servingsFromQuantity(
    ingredient.original_quantity || ingredient.quantity,
    ingredient.servings_per_container,
  );
  const packageUnits = new Set([
    "each",
    "bag",
    "box",
    "can",
    "bottle",
    "pack",
    "bunch",
    "head",
  ]);
  const unit = (ingredient.unit || "").toLowerCase();
  const showServingsAsQuantity =
    servingsLeft != null &&
    originalServings != null &&
    packageUnits.has(unit);

  const plainQuantityLabel = [ingredient.quantity, ingredient.unit]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={`rounded-2xl border border-stone-200 bg-stone-50 ${
        compact ? "p-3" : "p-4"
      }`}
    >
      {!compact && (
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="font-medium text-stone-900">{ingredient.name}</p>
            {ingredient.store_item_name &&
              ingredient.store_item_name !== ingredient.name && (
                <p className="text-xs text-stone-500">
                  Receipt: {ingredient.store_item_name}
                </p>
              )}
          </div>
          <div className="flex items-center gap-2">
            {showServingsAsQuantity ? (
              <span className="rounded-full bg-white px-2.5 py-1 text-xs text-stone-600 ring-1 ring-stone-200">
                ~{formatServingCount(originalServings)}{" "}
                {servingsNoun(originalServings)} ·{" "}
                <strong className="font-semibold text-stone-900">
                  {formatServingCount(servingsLeft)} left
                </strong>
              </span>
            ) : (
              plainQuantityLabel && (
                <span className="rounded-full bg-white px-2.5 py-1 text-xs font-medium text-stone-600 ring-1 ring-stone-200">
                  {plainQuantityLabel}
                </span>
              )
            )}
            {onRemove && (
              <button
                type="button"
                onClick={onRemove}
                disabled={removing}
                className="rounded-lg px-2 py-1 text-sm text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {removing ? "Removing..." : "Remove"}
              </button>
            )}
          </div>
        </div>
      )}

      {(ingredient.serving_size ||
        (servingsLeft != null && !showServingsAsQuantity)) && (
        <p className={`text-xs text-stone-500 ${compact ? "" : "mt-2"}`}>
          {ingredient.serving_size ? `Serving: ${ingredient.serving_size}` : null}
          {ingredient.serving_size &&
          servingsLeft != null &&
          !showServingsAsQuantity
            ? " · "
            : null}
          {servingsLeft != null && !showServingsAsQuantity && (
            <>
              ~{formatServingCount(originalServings ?? servingsLeft)}{" "}
              {servingsNoun(originalServings ?? servingsLeft)} ·{" "}
              <strong className="font-semibold text-stone-800">
                {formatServingCount(servingsLeft)} left
              </strong>
            </>
          )}
        </p>
      )}

      <NutritionFacts ingredient={ingredient} compact={compact} />

      {ingredient.nutrition_notes && (
        <p className="mt-2 text-xs italic text-stone-400">
          {ingredient.nutrition_notes}
        </p>
      )}
    </div>
  );
}

function NutritionFacts({
  ingredient,
  compact,
}: {
  ingredient: Ingredient;
  compact?: boolean;
}) {
  const facts = [
    { label: "Calories", value: ingredient.calories, unit: "" },
    { label: "Protein", value: ingredient.protein_g, unit: "g" },
    { label: "Carbs", value: ingredient.carbs_g, unit: "g" },
    { label: "Fat", value: ingredient.fat_g, unit: "g" },
    { label: "Fiber", value: ingredient.fiber_g, unit: "g" },
    { label: "Sodium", value: ingredient.sodium_mg, unit: "mg" },
  ].filter((fact) => fact.value != null);

  if (facts.length === 0) {
    return null;
  }

  return (
    <div
      className={`grid grid-cols-2 gap-2 sm:grid-cols-3 ${
        compact ? "mt-2" : "mt-3"
      }`}
    >
      {facts.map((fact) => (
        <div
          key={fact.label}
          className="rounded-xl bg-white px-3 py-2 ring-1 ring-stone-200"
        >
          <p className="text-[11px] uppercase tracking-wide text-stone-400">
            {fact.label}
          </p>
          <p className="text-sm font-semibold text-stone-800">
            {fact.value}
            {fact.unit}
          </p>
        </div>
      ))}
    </div>
  );
}
