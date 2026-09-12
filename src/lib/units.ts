export const PACKAGE_UNITS = [
  "each",
  "bag",
  "box",
  "can",
  "bottle",
  "pack",
  "bunch",
  "head",
] as const;

export const UNIT_GENERAL_HINT =
  "Units are saved as entered. Implausible units (e.g. watermelon in gallon) are rejected.";

export function isPackageUnit(unit: string | null | undefined): boolean {
  const normalized = (unit?.trim() || "each").toLowerCase();
  return PACKAGE_UNITS.includes(
    normalized as (typeof PACKAGE_UNITS)[number],
  );
}

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

const WEIGHT_TO_GRAMS: Record<string, number> = {
  g: 1,
  kg: 1000,
  oz: 28.3495,
  lb: 453.592,
};

const VOLUME_TO_ML: Record<string, number> = {
  ml: 1,
  l: 1000,
  cup: 236.588,
  tbsp: 14.787,
  tsp: 4.929,
  pint: 473.176,
  quart: 946.353,
  gallon: 3785.41,
  "fl oz": 29.5735,
};

export function convertAmount(
  quantity: number,
  fromUnit: string,
  toUnit: string,
): number | null {
  if (fromUnit === toUnit) return quantity;

  if (fromUnit in WEIGHT_TO_GRAMS && toUnit in WEIGHT_TO_GRAMS) {
    const grams = quantity * WEIGHT_TO_GRAMS[fromUnit]!;
    return grams / WEIGHT_TO_GRAMS[toUnit]!;
  }

  if (fromUnit in VOLUME_TO_ML && toUnit in VOLUME_TO_ML) {
    const ml = quantity * VOLUME_TO_ML[fromUnit]!;
    return ml / VOLUME_TO_ML[toUnit]!;
  }

  return null;
}

export function formatConvertedQuantity(value: number): string {
  const rounded = Math.round(value * 10000) / 10000;
  if (Math.abs(rounded - Math.round(rounded)) < 1e-6) {
    return String(Math.round(rounded));
  }
  return rounded.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}
