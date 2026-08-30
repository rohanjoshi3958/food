import type { Ingredient } from "@/lib/ingredients";
import {
  formatServingCount,
  getIngredientNutrition,
  getIngredientStock,
  scaleIngredientNutrition,
  servingSizeShortLabel,
  servingsNoun,
  shouldShowTotalOnHandNutrition,
} from "@/lib/ingredients";
import { MealMacrosCard } from "@/components/meal-macros";

export function IngredientNutritionDisplay({
  ingredient,
  compact = false,
}: {
  ingredient: Ingredient;
  compact?: boolean;
}) {
  const stock = getIngredientStock(ingredient);
  const perServing = getIngredientNutrition(ingredient);
  const hasNutrition = Object.values(perServing).some((value) => value != null);

  if (!hasNutrition) {
    return null;
  }

  const servingLabel = servingSizeShortLabel(ingredient.serving_size);
  const showTotalOnHand = shouldShowTotalOnHandNutrition(stock.servingsLeft);
  const totalOnHand =
    showTotalOnHand && stock.servingsLeft != null
      ? scaleIngredientNutrition(perServing, stock.servingsLeft)
      : null;

  const totalHeadingParts = ["Total on hand"];
  if (stock.servingsLeft != null) {
    totalHeadingParts.push(
      `(~${formatServingCount(stock.servingsLeft)} ${servingsNoun(stock.servingsLeft)})`,
    );
  }
  if (stock.quantityLabel) {
    totalHeadingParts.push(`· ${stock.quantityLabel}`);
  }

  return (
    <div className={compact ? "mt-2 space-y-2" : "mt-3 space-y-3"}>
      <div>
        <p className="text-xs font-medium text-stone-500">
          Per serving{servingLabel ? ` (${servingLabel})` : ""}
        </p>
        <MealMacrosCard macros={perServing} variant="chips" compact={compact} />
      </div>
      {totalOnHand && (
        <div>
          <p className="text-xs font-medium text-stone-500">
            {totalHeadingParts.join(" ")}
          </p>
          <MealMacrosCard
            macros={totalOnHand}
            variant="chips"
            compact={compact}
          />
        </div>
      )}
    </div>
  );
}
