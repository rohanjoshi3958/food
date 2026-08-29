"use client";

import { useEffect, useRef, useState } from "react";
import { MealMacrosCard } from "@/components/meal-macros";
import { apiFetch, parseError } from "@/lib/api";
import type { Ingredient } from "@/lib/ingredients";
import {
  parseMealIngredients,
  parseMealInstructionSteps,
  type Meal,
} from "@/lib/meals";

type Step = "suggest" | "cook" | "save";

function stepNumber(step: Step): 1 | 2 | 3 {
  if (step === "suggest") {
    return 1;
  }
  if (step === "cook") {
    return 2;
  }
  return 3;
}

function StepIndicator({ step }: { step: Step }) {
  const current = stepNumber(step);
  const steps = [
    { id: 1, label: "Suggest" },
    { id: 2, label: "Cook" },
    { id: 3, label: "Save" },
  ] as const;

  return (
    <ol className="flex items-center gap-2 text-xs font-medium sm:gap-4">
      {steps.map((item, index) => {
        const isComplete = item.id < current;
        const isActive = item.id === current;

        return (
          <li key={item.id} className="flex items-center gap-2 sm:gap-4">
            <span
              className={`flex items-center gap-2 ${
                isActive
                  ? "text-orange-600"
                  : isComplete
                    ? "text-stone-700"
                    : "text-stone-400"
              }`}
            >
              <span
                className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] ${
                  isActive
                    ? "bg-orange-500 text-white"
                    : isComplete
                      ? "bg-stone-800 text-white"
                      : "bg-stone-200 text-stone-500"
                }`}
              >
                {isComplete ? "✓" : item.id}
              </span>
              {item.label}
            </span>
            {index < steps.length - 1 && (
              <span
                className="hidden h-px w-6 bg-stone-200 sm:block"
                aria-hidden="true"
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function MealRecipeCard({ meal }: { meal: Meal }) {
  const ingredients = parseMealIngredients(meal.ingredients_used);
  const steps = parseMealInstructionSteps(meal.instructions);

  return (
    <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4 sm:p-6">
      <div className="flex flex-wrap items-start gap-3">
        <h3 className="text-lg font-semibold text-stone-900">{meal.name}</h3>
        {meal.calories != null && (
          <span className="rounded-full bg-orange-100 px-2.5 py-0.5 text-xs font-semibold text-orange-700">
            {Math.round(meal.calories)} cal
          </span>
        )}
      </div>

      {meal.description && (
        <p className="mt-2 text-sm text-stone-600">{meal.description}</p>
      )}

      <MealMacrosCard macros={meal} variant="chips" />

      {(ingredients.length > 0 || steps.length > 0) && (
        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          {ingredients.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                Ingredients
              </p>
              <ul className="mt-2 space-y-1.5 text-sm text-stone-700">
                {ingredients.map((item) => (
                  <li key={`${item.name}-${item.amount}`} className="flex gap-2">
                    <span className="text-stone-400">•</span>
                    <span>
                      <span className="font-medium text-stone-900">
                        {item.name}
                      </span>
                      {item.amount ? `: ${item.amount}` : null}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {steps.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                Instructions
              </p>
              <ol className="mt-2 list-decimal space-y-2 pl-5 text-sm text-stone-700">
                {steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PhotoDropZone({
  file,
  previewUrl,
  disabled,
  onFileSelected,
}: {
  file: File | null;
  previewUrl: string | null;
  disabled: boolean;
  onFileSelected: (file: File | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function pickFile(next: File | null) {
    if (!next) {
      onFileSelected(null);
      return;
    }

    if (!next.type.startsWith("image/")) {
      return;
    }

    onFileSelected(next);
  }

  function handleDrop(event: React.DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (disabled) {
      return;
    }

    pickFile(event.dataTransfer.files?.[0] ?? null);
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) {
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={`rounded-2xl border-2 border-dashed p-6 text-center transition ${
        dragging
          ? "border-orange-400 bg-orange-50"
          : "border-stone-300 bg-stone-50/80"
      } ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:border-orange-300"}`}
      onClick={() => {
        if (!disabled) {
          inputRef.current?.click();
        }
      }}
      onKeyDown={(event) => {
        if (disabled) {
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          inputRef.current?.click();
        }
      }}
      role="button"
      tabIndex={disabled ? -1 : 0}
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        className="hidden"
        disabled={disabled}
        onChange={(event) => pickFile(event.target.files?.[0] ?? null)}
      />

      {file && previewUrl ? (
        <div className="space-y-3">
          <img
            src={previewUrl}
            alt="Meal photo preview"
            className="mx-auto max-h-48 rounded-xl object-cover ring-1 ring-stone-200"
          />
          <p className="text-sm font-medium text-stone-800">{file.name}</p>
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onFileSelected(null);
            }}
            className="text-xs font-medium text-stone-500 underline-offset-2 hover:text-stone-700 hover:underline"
          >
            Remove photo
          </button>
        </div>
      ) : (
        <>
          <p className="text-sm font-medium text-stone-800">
            Drop photo or click to browse
          </p>
          <p className="mt-1 text-xs text-stone-500">JPG, PNG, WEBP, or GIF</p>
        </>
      )}
    </div>
  );
}

export function GenerateMealTab({
  refreshKey = 0,
  onIngredientsChanged,
  onCookbookChanged,
  onViewCookbook,
}: {
  refreshKey?: number;
  onIngredientsChanged?: () => void;
  onCookbookChanged?: () => void;
  onViewCookbook?: () => void;
}) {
  const [step, setStep] = useState<Step>("suggest");
  const [meal, setMeal] = useState<Meal | null>(null);
  const [ingredientCount, setIngredientCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedFile) {
      setPreviewUrl(null);
      return;
    }

    const objectUrl = URL.createObjectURL(selectedFile);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [selectedFile]);

  async function loadData() {
    setLoading(true);
    setError("");

    try {
      const [mealsResponse, ingredientsResponse] = await Promise.all([
        apiFetch("/api/meals"),
        apiFetch("/api/ingredients"),
      ]);

      if (!mealsResponse.ok) {
        throw new Error(await parseError(mealsResponse, "Unable to load meals."));
      }
      if (!ingredientsResponse.ok) {
        throw new Error(
          await parseError(ingredientsResponse, "Unable to load ingredients."),
        );
      }

      const meals: Meal[] = await mealsResponse.json();
      const ingredients: Ingredient[] = await ingredientsResponse.json();
      const currentMeal = meals[0] ?? null;

      setMeal(currentMeal);
      setIngredientCount(ingredients.length);
      setStep(currentMeal ? "cook" : "suggest");
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : "Unable to load data.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  async function handleSuggestMeal() {
    setGenerating(true);
    setError("");
    setMessage("");

    try {
      const response = await apiFetch("/api/meals/generate", {
        method: "POST",
        body: JSON.stringify(
          meal
            ? {
                previous_meal: {
                  name: meal.name,
                  description: meal.description,
                  ingredients_used: meal.ingredients_used,
                  instructions: meal.instructions,
                },
              }
            : {},
        ),
      });

      const data = await response.json();

      if (!response.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : "Unable to generate a meal.";
        throw new Error(detail);
      }

      setMeal(data as Meal);
      setStep("cook");
      setSelectedFile(null);
    } catch (generateError) {
      setError(
        generateError instanceof Error
          ? generateError.message
          : "Unable to generate a meal.",
      );
    } finally {
      setGenerating(false);
    }
  }

  async function handleSaveToCookbook() {
    if (!meal) {
      return;
    }

    setSaving(true);
    setError("");
    setMessage("");

    const formData = new FormData();
    if (selectedFile) {
      formData.append("file", selectedFile);
    }

    try {
      const response = await apiFetch(`/api/meals/${meal.id}/complete`, {
        method: "POST",
        body: formData,
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : "Unable to add meal to cookbook.";
        throw new Error(detail);
      }

      setMeal(null);
      setStep("suggest");
      setSelectedFile(null);
      setMessage("Meal saved to your cookbook.");
      onIngredientsChanged?.();
      onCookbookChanged?.();
      await loadData();
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Unable to add meal to cookbook.",
      );
    } finally {
      setSaving(false);
    }
  }

  const savingLabel = selectedFile
    ? "Saving to cookbook..."
    : "Generating meal image...";

  const indicatorStep: Step = generating ? "suggest" : step;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">Generate meal</h2>
        <p className="mt-1 text-sm text-stone-600">
          One-person suggestions from your pantry (~500–800 cal)
        </p>
      </div>

      <StepIndicator step={indicatorStep} />

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {message && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-green-50 px-4 py-3 text-sm text-green-700">
          <span>{message}</span>
          {onViewCookbook && (
            <button
              type="button"
              onClick={onViewCookbook}
              className="rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-green-800 ring-1 ring-green-200 transition hover:bg-green-100"
            >
              View cookbook
            </button>
          )}
        </div>
      )}

      {generating && (
        <div className="rounded-2xl border border-orange-200 bg-orange-50 px-4 py-5">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 h-5 w-5 animate-spin rounded-full border-2 border-orange-200 border-t-orange-600" />
            <div>
              <p className="font-medium text-stone-900">Building your meal…</p>
              <p className="mt-1 text-sm text-stone-600">
                Picking a one-person recipe from what&apos;s in your kitchen.
              </p>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-stone-500">Loading...</p>
      ) : step === "suggest" && !generating ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-3">
            <p className="text-sm text-stone-700">
              {ingredientCount === 0 ? (
                "No ingredients in your kitchen yet."
              ) : (
                <>
                  <span className="font-semibold text-stone-900">
                    {ingredientCount}
                  </span>{" "}
                  ingredient{ingredientCount === 1 ? "" : "s"} in stock
                </>
              )}
            </p>
            <button
              type="button"
              onClick={handleSuggestMeal}
              disabled={generating || ingredientCount === 0}
              className="rounded-xl bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Suggest a meal
            </button>
          </div>

          {!meal && (
            <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50/60 px-6 py-12 text-center">
              <p className="text-sm font-medium text-stone-800">
                Ready for a suggestion?
              </p>
              <p className="mt-1 text-sm text-stone-500">
                We&apos;ll pick a one-person meal from what&apos;s in your
                kitchen.
              </p>
            </div>
          )}
        </div>
      ) : null}

      {step === "cook" && meal && !generating && (
        <div className="space-y-4">
          <MealRecipeCard meal={meal} />

          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={handleSuggestMeal}
              disabled={generating || saving}
              className="rounded-xl border border-stone-200 px-4 py-2.5 text-sm font-medium text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Try another suggestion
            </button>
            <button
              type="button"
              onClick={() => {
                setSelectedFile(null);
                setStep("save");
                setError("");
              }}
              disabled={saving}
              className="rounded-xl bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Proceed to save →
            </button>
          </div>
        </div>
      )}

      {step === "save" && meal && (
        <div className="space-y-4">
          <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-semibold text-stone-900">{meal.name}</p>
              {meal.calories != null && (
                <span className="rounded-full bg-orange-100 px-2.5 py-0.5 text-xs font-semibold text-orange-700">
                  {Math.round(meal.calories)} cal
                </span>
              )}
            </div>
            <MealMacrosCard macros={meal} variant="chips" compact />
          </div>

          <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-stone-900">
              Save to cookbook
            </h3>
            <p className="mt-1 text-sm text-stone-500">
              Optional: upload a photo of what you made. If you skip, we&apos;ll
              generate an AI image.
            </p>

            <div className="mt-4">
              <PhotoDropZone
                file={selectedFile}
                previewUrl={previewUrl}
                disabled={saving}
                onFileSelected={setSelectedFile}
              />
            </div>

            <div className="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => {
                  setStep("cook");
                  setError("");
                }}
                disabled={saving}
                className="rounded-xl border border-stone-200 px-4 py-2.5 text-sm font-medium text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Back to recipe
              </button>
              <button
                type="button"
                onClick={handleSaveToCookbook}
                disabled={saving}
                className="rounded-xl bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {saving ? savingLabel : "Save to cookbook"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
