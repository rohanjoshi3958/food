export type MealMacros = {
  calories: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  sodium_mg: number | null;
};

export function MealMacrosCard({
  macros,
  compact = false,
  variant = "grid",
}: {
  macros: MealMacros;
  compact?: boolean;
  variant?: "grid" | "chips";
}) {
  const facts = [
    { label: "Calories", value: macros.calories, unit: "" },
    { label: "Protein", value: macros.protein_g, unit: "g" },
    { label: "Carbs", value: macros.carbs_g, unit: "g" },
    { label: "Fat", value: macros.fat_g, unit: "g" },
    { label: "Fiber", value: macros.fiber_g, unit: "g" },
    { label: "Sodium", value: macros.sodium_mg, unit: "mg" },
  ].filter((fact) => fact.value != null);

  if (facts.length === 0) {
    return null;
  }

  if (variant === "chips") {
    return (
      <div className={`flex flex-wrap gap-2 ${compact ? "" : "mt-3"}`}>
        {facts.map((fact) => (
          <span
            key={fact.label}
            className="rounded-full bg-white px-3 py-1 text-xs font-medium text-stone-700 ring-1 ring-stone-200"
          >
            {fact.label} {fact.value}
            {fact.unit}
          </span>
        ))}
      </div>
    );
  }

  return (
    <div className={compact ? "" : "mt-3"}>
      {!compact && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
            Macros
          </p>
          <p className="text-xs text-stone-400">
            Estimated for one person from ingredients used
          </p>
        </div>
      )}
      <div
        className={`grid grid-cols-2 gap-2 sm:grid-cols-3 ${
          compact ? "mt-2" : "mt-2"
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
    </div>
  );
}
