import { INGREDIENT_UNITS, isKnownUnit } from "@/lib/units";

const selectClassName =
  "w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-900 outline-none transition focus:border-orange-400 focus:bg-white focus:ring-4 focus:ring-orange-100";

export function UnitSelect({
  value,
  onChange,
  disabled = false,
  required = true,
  className = selectClassName,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  required?: boolean;
  className?: string;
}) {
  const extraOption =
    value && !isKnownUnit(value)
      ? [{ value, label: value }]
      : [];

  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      required={required}
      className={className}
    >
      <option value="" disabled>
        Select unit
      </option>
      {extraOption.map((unit) => (
        <option key={unit.value} value={unit.value}>
          {unit.label}
        </option>
      ))}
      {INGREDIENT_UNITS.map((unit) => (
        <option key={unit.value} value={unit.value}>
          {unit.label}
        </option>
      ))}
    </select>
  );
}
