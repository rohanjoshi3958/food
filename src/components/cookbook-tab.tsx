"use client";

import { useEffect, useState } from "react";
import { MealMacrosCard } from "@/components/meal-macros";
import { apiFetch, parseError } from "@/lib/api";
import {
  formatCookbookDate,
  parseMealIngredients,
  parseMealInstructionSteps,
} from "@/lib/meals";

type CookbookEntry = {
  id: string;
  title: string;
  description: string | null;
  ingredients: string | null;
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

function CookbookPhoto({
  entryId,
  photoUrl,
  className = "",
}: {
  entryId: string;
  photoUrl: string | null;
  className?: string;
}) {
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(photoUrl));

  useEffect(() => {
    if (!photoUrl) {
      setImageSrc(null);
      setLoading(false);
      return;
    }

    let objectUrl: string | null = null;
    let cancelled = false;

    async function loadPhoto() {
      setLoading(true);

      try {
        const response = await apiFetch(`/api/cookbook/${entryId}/photo`);
        if (!response.ok) {
          throw new Error("Unable to load photo.");
        }

        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);

        if (!cancelled) {
          setImageSrc(objectUrl);
        }
      } catch {
        if (!cancelled) {
          setImageSrc(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadPhoto();

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [entryId, photoUrl]);

  if (!photoUrl) {
    return (
      <div
        className={`flex items-center justify-center bg-stone-200/80 text-stone-500 ${className}`}
      >
        <span className="text-xs font-medium">No photo</span>
      </div>
    );
  }

  if (loading) {
    return (
      <div
        className={`animate-pulse bg-stone-200/80 ${className}`}
        aria-hidden="true"
      />
    );
  }

  if (!imageSrc) {
    return (
      <div
        className={`flex items-center justify-center bg-stone-200/80 text-stone-500 ${className}`}
      >
        <span className="text-xs font-medium">No photo</span>
      </div>
    );
  }

  return (
    <img
      src={imageSrc}
      alt=""
      className={`object-cover ${className}`}
    />
  );
}

function CookbookCard({
  entry,
  selected,
  onSelect,
}: {
  entry: CookbookEntry;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`overflow-hidden rounded-2xl border bg-white text-left transition hover:shadow-md ${
        selected
          ? "border-orange-400 ring-2 ring-orange-200"
          : "border-stone-200 hover:border-stone-300"
      }`}
    >
      <CookbookPhoto
        entryId={entry.id}
        photoUrl={entry.photo_url}
        className="aspect-[4/3] w-full"
      />
      <div className="space-y-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="line-clamp-2 font-medium text-stone-900">{entry.title}</p>
          {entry.calories != null && (
            <span className="shrink-0 rounded-full bg-orange-100 px-2.5 py-0.5 text-xs font-semibold text-orange-700">
              {Math.round(entry.calories)} cal
            </span>
          )}
        </div>
        {entry.description && (
          <p className="line-clamp-2 text-sm text-stone-600">{entry.description}</p>
        )}
        {entry.created_at && (
          <p className="text-xs text-stone-400">
            Added {formatCookbookDate(entry.created_at)}
          </p>
        )}
      </div>
    </button>
  );
}

function CookbookDetail({
  entry,
  onClose,
  onDelete,
  deleting,
}: {
  entry: CookbookEntry;
  onClose: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const ingredients = parseMealIngredients(entry.ingredients);
  const steps = parseMealInstructionSteps(entry.instructions);

  return (
    <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4 sm:p-6">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-stone-900">{entry.title}</h3>
          {entry.created_at && (
            <p className="mt-1 text-xs text-stone-400">
              Added {formatCookbookDate(entry.created_at)}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-sm text-stone-600 transition hover:bg-stone-200/60"
          >
            Close
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting}
            className="rounded-lg px-2 py-1 text-sm text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {deleting ? "Deleting..." : "Delete"}
          </button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <CookbookPhoto
          entryId={entry.id}
          photoUrl={entry.photo_url}
          className="aspect-[4/3] w-full rounded-2xl ring-1 ring-stone-200 lg:aspect-square"
        />

        <div className="space-y-4">
          {entry.description && (
            <p className="text-sm text-stone-600">{entry.description}</p>
          )}

          <MealMacrosCard macros={entry} variant="chips" />

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
                      <span className="font-medium text-stone-900">{item.name}</span>
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
      </div>
    </div>
  );
}

export function CookbookTab() {
  const [entries, setEntries] = useState<CookbookEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);

  useEffect(() => {
    async function loadEntries() {
      setLoading(true);
      setError("");

      try {
        const response = await apiFetch("/api/cookbook");
        if (!response.ok) {
          throw new Error(await parseError(response, "Unable to load cookbook."));
        }

        setEntries(await response.json());
      } catch (loadError) {
        setError(
          loadError instanceof Error
            ? loadError.message
            : "Unable to load cookbook.",
        );
      } finally {
        setLoading(false);
      }
    }

    loadEntries();
  }, []);

  async function removeEntry(id: string) {
    setRemovingId(id);
    setError("");

    try {
      const response = await apiFetch(`/api/cookbook/${id}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        throw new Error(await parseError(response, "Unable to delete meal."));
      }

      setEntries((current) => current.filter((entry) => entry.id !== id));
      if (selectedId === id) {
        setSelectedId(null);
      }
    } catch (removeError) {
      setError(
        removeError instanceof Error
          ? removeError.message
          : "Unable to delete meal.",
      );
    } finally {
      setRemovingId(null);
    }
  }

  function handleSelect(id: string) {
    setSelectedId((current) => (current === id ? null : id));
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">Your cookbook</h2>
        <p className="mt-1 text-sm text-stone-600">Meals you&apos;ve saved</p>
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((key) => (
            <div
              key={key}
              className="overflow-hidden rounded-2xl border border-stone-200 bg-white"
            >
              <div className="aspect-[4/3] animate-pulse bg-stone-200" />
              <div className="space-y-2 p-4">
                <div className="h-4 w-2/3 animate-pulse rounded bg-stone-200" />
                <div className="h-3 w-full animate-pulse rounded bg-stone-100" />
              </div>
            </div>
          ))}
        </div>
      ) : entries.length === 0 ? (
        <p className="text-sm text-stone-500">
          No cookbook entries yet. Generate a meal and add it to your cookbook
          to see it here.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {entries.map((entry) => (
            <div key={entry.id} className="contents">
              <CookbookCard
                entry={entry}
                selected={selectedId === entry.id}
                onSelect={() => handleSelect(entry.id)}
              />
              {selectedId === entry.id && (
                <div className="col-span-full">
                  <CookbookDetail
                    entry={entry}
                    onClose={() => setSelectedId(null)}
                    onDelete={() => removeEntry(entry.id)}
                    deleting={removingId === entry.id}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
