"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  apiFetch,
  AuthUser,
  getCurrentUser,
  getToken,
  logout,
  parseError,
} from "@/lib/api";
import { CookbookTab } from "@/components/cookbook-tab";
import { IngredientsTab } from "@/components/ingredients-tab";
import { ReceiptUploadTab } from "@/components/receipt-upload-tab";
import { MealMacrosCard } from "@/components/meal-macros";
import { formatMealInstructions, type Meal } from "@/lib/meals";

type TabId = "receipt" | "ingredients" | "meals" | "cookbook";

const tabs: { id: TabId; label: string }[] = [
  { id: "receipt", label: "Upload a receipt" },
  { id: "ingredients", label: "View ingredients" },
  { id: "meals", label: "Generate meal" },
  { id: "cookbook", label: "View cookbook" },
];

export function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>(() => {
    if (typeof window === "undefined") {
      return "receipt";
    }

    const tab = new URLSearchParams(window.location.search).get("tab");
    return tabs.some((item) => item.id === tab) ? (tab as TabId) : "receipt";
  });
  const [loading, setLoading] = useState(true);
  const [ingredientsRefreshKey, setIngredientsRefreshKey] = useState(0);

  function refreshIngredients() {
    setIngredientsRefreshKey((current) => current + 1);
  }

  useEffect(() => {
    getCurrentUser()
      .then((currentUser) => {
        if (!currentUser) {
          router.replace("/login");
          return;
        }

        setUser(currentUser);
        setLoading(false);
      })
      .catch(() => {
        router.replace("/login");
      });
  }, [router]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const params = new URLSearchParams(window.location.search);
    if (params.get("tab") === "meals") {
      refreshIngredients();
    }

    if (params.has("tab")) {
      params.delete("tab");
      const next = params.toString();
      window.history.replaceState(null, "", next ? `/?${next}` : "/");
    }
  }, []);

  function handleLogout() {
    const token = getToken();
    if (token) {
      void fetch("/api/receipts/discard-pending", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        keepalive: true,
      });
    }
    logout();
    router.replace("/login");
  }

  if (loading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center bg-stone-50">
        <p className="text-sm text-stone-500">Loading your kitchen...</p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col bg-stone-50">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-500 text-lg shadow-md shadow-orange-500/20">
              🍽️
            </div>
            <div>
              <h1 className="text-lg font-semibold text-stone-900">
                Welcome{user.name ? `, ${user.name}` : ""}
              </h1>
              <p className="text-sm text-stone-500">{user.email}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            className="rounded-xl border border-stone-200 px-4 py-2 text-sm font-medium text-stone-700 transition hover:bg-stone-50"
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-8">
        <nav className="flex flex-wrap gap-2">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`rounded-full px-4 py-2 text-sm font-medium transition ${
                activeTab === tab.id
                  ? "bg-orange-500 text-white shadow-md shadow-orange-500/20"
                  : "border border-stone-200 bg-white text-stone-600 hover:bg-stone-100"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <section className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
          <div className={activeTab === "receipt" ? "block" : "hidden"}>
            <ReceiptUploadTab
              onIngredientsChanged={refreshIngredients}
              onViewIngredients={() => setActiveTab("ingredients")}
            />
          </div>
          <div className={activeTab === "ingredients" ? "block" : "hidden"}>
            <IngredientsTab refreshKey={ingredientsRefreshKey} />
          </div>
          <div className={activeTab === "meals" ? "block" : "hidden"}>
            <GenerateMealTab />
          </div>
          <div className={activeTab === "cookbook" ? "block" : "hidden"}>
            <CookbookTab />
          </div>
        </section>
      </main>
    </div>
  );
}

function GenerateMealTab() {
  const router = useRouter();
  const [meal, setMeal] = useState<Meal | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");

  async function loadMeal() {
    setLoading(true);
    setError("");

    try {
      const response = await apiFetch("/api/meals");
      if (!response.ok) {
        throw new Error(await parseError(response, "Unable to load meals."));
      }

      const meals: Meal[] = await response.json();
      setMeal(meals[0] ?? null);
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load meals.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadMeal();
  }, []);

  async function handleGenerateMeal() {
    setGenerating(true);
    setError("");

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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">Generate meal</h2>
        <p className="mt-1 text-sm text-stone-600">
          Create a one-person meal suggestion from the ingredients in your kitchen.
        </p>
      </div>

      <button
        type="button"
        onClick={handleGenerateMeal}
        disabled={generating}
        className="rounded-xl bg-orange-500 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {generating ? "Generating meal..." : "Generate meal"}
      </button>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-stone-500">Loading...</p>
      ) : meal ? (
        <div className="space-y-4">
          <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4">
            <p className="font-medium text-stone-900">{meal.name}</p>
            {meal.description && (
              <p className="mt-1 text-sm text-stone-600">{meal.description}</p>
            )}
            <MealMacrosCard macros={meal} />
            {meal.ingredients_used && (
              <div className="mt-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                  Ingredients to use
                </p>
                <pre className="mt-1 whitespace-pre-wrap font-sans text-sm text-stone-700">
                  {meal.ingredients_used}
                </pre>
              </div>
            )}
            {meal.instructions && (
              <div className="mt-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                  Instructions
                </p>
                <pre className="mt-1 whitespace-pre-wrap font-sans text-sm text-stone-700">
                  {formatMealInstructions(meal.instructions)}
                </pre>
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => router.push(`/meals/${meal.id}`)}
            className="rounded-xl bg-orange-500 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600"
          >
            Proceed with meal
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ListTab<T extends { id: string }>({
  title,
  emptyMessage,
  endpoint,
  refreshKey = 0,
  renderItem,
}: {
  title: string;
  emptyMessage: string;
  endpoint: string;
  refreshKey?: number;
  renderItem: (item: T) => React.ReactNode;
}) {
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadItems() {
      setLoading(true);
      setError("");

      try {
        const response = await apiFetch(endpoint);
        if (!response.ok) {
          throw new Error(await parseError(response, "Unable to load data."));
        }

        setItems(await response.json());
      } catch (loadError) {
        setError(
          loadError instanceof Error
            ? loadError.message
            : "Unable to load data.",
        );
      } finally {
        setLoading(false);
      }
    }

    loadItems();
  }, [endpoint, refreshKey]);

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-stone-900">{title}</h2>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-stone-500">Loading...</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-stone-500">{emptyMessage}</p>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id}>{renderItem(item)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
