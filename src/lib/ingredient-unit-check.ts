import { apiFetch, readJsonResponse } from "@/lib/api";

type CheckUnitResponse = {
  warning?: string | null;
};

export async function fetchIngredientUnitWarning(
  ingredientName: string,
  unit: string,
): Promise<string | null> {
  const response = await apiFetch("/api/ingredients/unit-check", {
    method: "POST",
    body: JSON.stringify({
      ingredient_name: ingredientName.trim(),
      unit: unit.trim(),
    }),
  });
  const data = await readJsonResponse<CheckUnitResponse>(response);

  if (!response.ok) {
    return null;
  }

  return data.warning?.trim() || null;
}

export async function findFirstUnitWarning(
  items: Array<{ ingredient_name: string; unit: string }>,
): Promise<string | null> {
  for (const item of items) {
    const name = item.ingredient_name.trim();
    const unit = item.unit.trim();
    if (!name || !unit) {
      continue;
    }

    const warning = await fetchIngredientUnitWarning(name, unit);
    if (warning) {
      return `"${name}": ${warning}`;
    }
  }

  return null;
}
