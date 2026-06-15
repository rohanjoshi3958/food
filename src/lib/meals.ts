export function formatMealInstructions(text: string): string {
  const parts = text.split(/\s+(?=\d+\.\s)/);
  if (parts.length > 1) {
    return parts.map((part) => part.trim()).join("\n");
  }

  return text;
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
