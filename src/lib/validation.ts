import { isPackageUnit } from "@/lib/units";

export const QUANTITY_HINT =
  "Numbers only — e.g. 1, 1.5, or 1,000. Use commas for thousands.";

export const PACKAGE_QUANTITY_HINT =
  "Whole numbers only — e.g. 1, 2, or 1,000.";

export const INGREDIENT_NAME_HINT =
  "Use a specific grocery item name. Unrecognized names are rejected when saved.";

export function getQuantityHint(unit: string | null | undefined): string {
  return isPackageUnit(unit) ? PACKAGE_QUANTITY_HINT : QUANTITY_HINT;
}

function isValidCommaGroupedDigits(value: string): boolean {
  if (!value) {
    return false;
  }
  if (!value.includes(",")) {
    return /^\d+$/.test(value);
  }

  const parts = value.split(",");
  if (!/^\d+$/.test(parts[0]!) || parts[0]!.length < 1 || parts[0]!.length > 3) {
    return false;
  }
  for (let index = 1; index < parts.length; index += 1) {
    const part = parts[index]!;
    if (part.length !== 3 || !/^\d+$/.test(part)) {
      return false;
    }
  }
  return true;
}

function parseQuantityValue(quantity: string): number | null {
  const dotIndex = quantity.indexOf(".");
  const integerPart =
    dotIndex === -1 ? quantity : quantity.slice(0, dotIndex);
  const fractionalPart =
    dotIndex === -1 ? "" : quantity.slice(dotIndex + 1);

  if (dotIndex !== -1 && fractionalPart.includes(".")) {
    return null;
  }
  if (!integerPart) {
    return null;
  }
  if (!isValidCommaGroupedDigits(integerPart)) {
    return null;
  }
  if (fractionalPart && !/^\d+$/.test(fractionalPart)) {
    return null;
  }

  const normalized =
    integerPart.replaceAll(",", "") +
    (fractionalPart ? `.${fractionalPart}` : "");
  const value = Number.parseFloat(normalized);
  return Number.isFinite(value) ? value : null;
}

export function validateQuantity(
  quantity: string | null | undefined,
  unit?: string | null,
): string | null {
  if (quantity == null || quantity.trim() === "") {
    return null;
  }

  const trimmed = quantity.trim();

  if (trimmed.startsWith("-")) {
    return "Quantity cannot be negative.";
  }

  const value = parseQuantityValue(trimmed);
  if (value == null) {
    return "Quantity must be a number.";
  }

  if (isPackageUnit(unit) && trimmed.includes(".")) {
    return "Package quantities must be whole numbers.";
  }

  if (value === 0) {
    return "Quantity must be greater than zero.";
  }

  return null;
}
