export const INGREDIENT_UNITS = [
  { value: "each", label: "Each" },
  { value: "lb", label: "Pound (lb)" },
  { value: "oz", label: "Ounce (oz)" },
  { value: "g", label: "Gram (g)" },
  { value: "kg", label: "Kilogram (kg)" },
  { value: "ml", label: "Milliliter (ml)" },
  { value: "l", label: "Liter (L)" },
  { value: "fl oz", label: "Fluid ounce (fl oz)" },
  { value: "cup", label: "Cup" },
  { value: "pint", label: "Pint" },
  { value: "quart", label: "Quart" },
  { value: "gallon", label: "Gallon" },
  { value: "tbsp", label: "Tablespoon (tbsp)" },
  { value: "tsp", label: "Teaspoon (tsp)" },
  { value: "bunch", label: "Bunch" },
  { value: "bag", label: "Bag" },
  { value: "box", label: "Box" },
  { value: "can", label: "Can" },
  { value: "bottle", label: "Bottle" },
  { value: "pack", label: "Pack" },
  { value: "slice", label: "Slice" },
  { value: "head", label: "Head" },
  { value: "clove", label: "Clove" },
] as const;

export const INGREDIENT_UNIT_VALUES = INGREDIENT_UNITS.map((unit) => unit.value);

export function isKnownUnit(unit: string) {
  return INGREDIENT_UNIT_VALUES.includes(
    unit as (typeof INGREDIENT_UNIT_VALUES)[number],
  );
}
