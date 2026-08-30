"use client";

import { useEffect, useState } from "react";
import { apiFetch, readJsonResponse } from "@/lib/api";

type CheckUnitResponse = {
  warning?: string | null;
};

export function useIngredientUnitWarning(
  name: string,
  unit: string,
  options?: { enabled?: boolean; debounceMs?: number },
) {
  const enabled = options?.enabled ?? true;
  const debounceMs = options?.debounceMs ?? 600;
  const [warning, setWarning] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const trimmedName = name.trim();
    const trimmedUnit = unit.trim();

    if (!enabled || !trimmedName || !trimmedUnit) {
      setWarning(null);
      setChecking(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setChecking(true);

      try {
        const response = await apiFetch("/api/ingredients/unit-check", {
          method: "POST",
          body: JSON.stringify({
            ingredient_name: trimmedName,
            unit: trimmedUnit,
          }),
          signal: controller.signal,
        });
        const data = await readJsonResponse<CheckUnitResponse>(response);

        if (!controller.signal.aborted) {
          setWarning(response.ok ? data.warning ?? null : null);
        }
      } catch {
        if (!controller.signal.aborted) {
          setWarning(null);
        }
      } finally {
        if (!controller.signal.aborted) {
          setChecking(false);
        }
      }
    }, debounceMs);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [name, unit, enabled, debounceMs]);

  return { warning, checking };
}
