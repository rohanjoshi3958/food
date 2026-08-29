export function formatMealInstructions(text: string): string {
  const parts = text.split(/\s+(?=\d+\.\s)/);
  if (parts.length > 1) {
    return parts.map((part) => part.trim()).join("\n");
  }

  return text;
}

export function parseMealIngredients(
  text: string | null,
): { name: string; amount: string }[] {
  if (!text) {
    return [];
  }

  const items: { name: string; amount: string }[] = [];

  for (const line of text.split("\n")) {
    const cleaned = line.trim().replace(/^-\s*/, "");
    if (!cleaned || !cleaned.includes(":")) {
      continue;
    }

    const colonIndex = cleaned.indexOf(":");
    const name = cleaned.slice(0, colonIndex).trim();
    const amount = cleaned.slice(colonIndex + 1).trim();
    if (name) {
      items.push({ name, amount });
    }
  }

  return items;
}

export function parseMealInstructionSteps(text: string | null): string[] {
  if (!text) {
    return [];
  }

  return formatMealInstructions(text)
    .split("\n")
    .map((step) => step.replace(/^\d+\.\s*/, "").trim())
    .filter(Boolean);
}

export function formatCookbookDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatCookbookDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export type Meal = {
  id: string;
  name: string;
  description: string | null;
  ingredients_used: string | null;
  instructions: string | null;
  photo_url: string | null;
  calories: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  sodium_mg: number | null;
  created_at: string;
};
