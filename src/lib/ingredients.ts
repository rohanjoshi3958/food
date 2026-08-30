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

export function formatServingCount(value: number): string {
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

export function servingsNoun(value: number): string {
  const rounded =
    Math.abs(value - Math.round(value)) < 0.05 ? Math.round(value) : value;
  return rounded === 1 ? "serving" : "servings";
}

export function servingsFromQuantity(
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

export type IngredientStock = {
  servingsLeft: number | null;
  originalServings: number | null;
  stockRatio: number | null;
  isLowStock: boolean;
  quantityLabel: string | null;
};

export function getIngredientStock(ingredient: Ingredient): IngredientStock {
  const servingsLeft = servingsFromQuantity(
    ingredient.quantity,
    ingredient.servings_per_container,
  );
  const originalServings = servingsFromQuantity(
    ingredient.original_quantity || ingredient.quantity,
    ingredient.servings_per_container,
  );

  const stockRatio =
    servingsLeft != null &&
    originalServings != null &&
    originalServings > 0
      ? servingsLeft / originalServings
      : null;

  const isLowStock =
    servingsLeft != null &&
    stockRatio != null &&
    originalServings != null &&
    originalServings > 1 &&
    stockRatio <= 0.25;

  const quantityLabel = [ingredient.quantity, ingredient.unit]
    .filter(Boolean)
    .join(" ");

  return {
    servingsLeft,
    originalServings,
    stockRatio,
    isLowStock,
    quantityLabel: quantityLabel || null,
  };
}

export function ingredientSummaryLine(ingredient: Ingredient): string | null {
  const stock = getIngredientStock(ingredient);
  const parts: string[] = [];

  if (ingredient.calories != null) {
    parts.push(`${Math.round(ingredient.calories)} cal/serving`);
  }

  if (ingredient.serving_size) {
    parts.push(ingredient.serving_size);
  } else if (stock.quantityLabel) {
    parts.push(stock.quantityLabel);
  }

  return parts.length > 0 ? parts.join(" · ") : null;
}
