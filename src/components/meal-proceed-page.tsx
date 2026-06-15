"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, getCurrentUser, parseError } from "@/lib/api";
import { formatMealInstructions, type Meal } from "@/lib/meals";
import { MealMacrosCard } from "@/components/meal-macros";

function MealPhoto({
  mealId,
  photoUrl,
  refreshKey,
}: {
  mealId: string;
  photoUrl: string | null;
  refreshKey: number;
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
        const response = await apiFetch(`/api/meals/${mealId}/photo`);
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
  }, [mealId, photoUrl, refreshKey]);

  if (loading) {
    return <p className="text-sm text-stone-500">Loading photo...</p>;
  }

  if (!imageSrc) {
    return null;
  }

  return (
    <img
      src={imageSrc}
      alt="Your meal"
      className="max-h-80 w-full rounded-2xl object-cover ring-1 ring-stone-200"
    />
  );
}

export function MealProceedPage({ mealId }: { mealId: string }) {
  const router = useRouter();
  const [meal, setMeal] = useState<Meal | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [photoRefreshKey, setPhotoRefreshKey] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    getCurrentUser()
      .then((user) => {
        if (!user) {
          router.replace("/login");
        }
      })
      .catch(() => {
        router.replace("/login");
      });
  }, [router]);

  useEffect(() => {
    async function loadMeal() {
      setLoading(true);
      setError("");

      try {
        const response = await apiFetch(`/api/meals/${mealId}`);
        if (!response.ok) {
          throw new Error(await parseError(response, "Unable to load meal."));
        }

        setMeal(await response.json());
      } catch (loadError) {
        setError(
          loadError instanceof Error ? loadError.message : "Unable to load meal.",
        );
      } finally {
        setLoading(false);
      }
    }

    loadMeal();
  }, [mealId]);

  async function handlePhotoUpload(file: File | null) {
    if (!file || !meal) {
      return;
    }

    setUploading(true);
    setError("");
    setMessage("");

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await apiFetch(`/api/meals/${meal.id}/photo`, {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (!response.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : "Unable to upload photo.";
        throw new Error(detail);
      }

      setMeal(data as Meal);
      setPhotoRefreshKey((current) => current + 1);
      setMessage("Photo uploaded and added to your cookbook. Your ingredients were updated.");
    } catch (uploadError) {
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : "Unable to upload photo.",
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col bg-stone-50">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <Link
            href="/"
            className="text-sm font-medium text-stone-600 transition hover:text-stone-900"
          >
            Back to dashboard
          </Link>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
        {loading ? (
          <p className="text-sm text-stone-500">Loading meal...</p>
        ) : error && !meal ? (
          <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
            {error}
          </p>
        ) : meal ? (
          <div className="space-y-6">
            <div>
              <h1 className="text-2xl font-semibold text-stone-900">{meal.name}</h1>
              {meal.description && (
                <p className="mt-2 text-sm text-stone-600">{meal.description}</p>
              )}
              <MealMacrosCard macros={meal} />
            </div>

            <div className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
              {meal.ingredients_used && (
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                    Ingredients to use
                  </p>
                  <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-stone-700">
                    {meal.ingredients_used}
                  </pre>
                </div>
              )}

              {meal.instructions && (
                <div className={meal.ingredients_used ? "mt-6" : ""}>
                  <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                    Instructions
                  </p>
                  <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-stone-700">
                    {formatMealInstructions(meal.instructions)}
                  </pre>
                </div>
              )}
            </div>

            <div className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
              <h2 className="text-lg font-semibold text-stone-900">Meal photo</h2>
              <p className="mt-1 text-sm text-stone-600">
                Upload a picture of the meal you made.
              </p>

              <div className="mt-4 space-y-4">
                <MealPhoto
                  mealId={meal.id}
                  photoUrl={meal.photo_url}
                  refreshKey={photoRefreshKey}
                />

                <label className="block">
                  <span className="sr-only">Choose meal photo</span>
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp,image/gif"
                    disabled={uploading}
                    onChange={(event) => {
                      const file = event.target.files?.[0] ?? null;
                      void handlePhotoUpload(file);
                      event.target.value = "";
                    }}
                    className="block w-full text-sm text-stone-600 file:mr-4 file:rounded-xl file:border-0 file:bg-orange-500 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white hover:file:bg-orange-600"
                  />
                </label>

                {uploading && (
                  <p className="text-sm text-stone-500">Uploading photo...</p>
                )}
              </div>
            </div>

            {error && (
              <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
                {error}
              </p>
            )}

            {message && (
              <p className="rounded-xl bg-green-50 px-4 py-3 text-sm text-green-700">
                {message}
              </p>
            )}
          </div>
        ) : null}
      </main>
    </div>
  );
}
