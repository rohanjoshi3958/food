export type Ingredient = {
  id: string;
  name: string;
  store_item_name: string | null;
  quantity: string | null;
  unit: string | null;
  serving_size: string | null;
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

export function IngredientCard({
  ingredient,
  compact = false,
}: {
  ingredient: Ingredient;
  compact?: boolean;
}) {
  const quantityLabel = [ingredient.quantity, ingredient.unit]
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
          {quantityLabel && (
            <span className="rounded-full bg-white px-2.5 py-1 text-xs font-medium text-stone-600 ring-1 ring-stone-200">
              {quantityLabel}
            </span>
          )}
        </div>
      )}

      {ingredient.serving_size && (
        <p className={`text-xs text-stone-500 ${compact ? "" : "mt-2"}`}>
          Serving: {ingredient.serving_size}
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
