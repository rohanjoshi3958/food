"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  apiFetch,
  AuthUser,
  getCurrentUser,
  logout,
  parseError,
} from "@/lib/api";
import { IngredientCard, type Ingredient } from "@/components/ingredient-card";
import { ManualIngredientList } from "@/components/manual-ingredient-list";
import { ReceiptReview } from "@/components/receipt-review";

type TabId = "receipt" | "ingredients" | "meals" | "cookbook";

type DraftItem = {
  store_item_name: string;
  ingredient_name: string;
  quantity: string | null;
  unit: string | null;
  serving_size: string | null;
  calories: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  sodium_mg: number | null;
  nutrition_notes: string | null;
  is_manual: boolean;
};

type Receipt = {
  id: string;
  original_name: string;
  store_name: string | null;
  analysis_status: string;
  analysis_error: string | null;
  uploaded_at: string;
  ingredients: Ingredient[];
  draft_items: DraftItem[];
};

type Meal = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
};

type CookbookEntry = {
  id: string;
  title: string;
  ingredients: string | null;
  instructions: string | null;
  created_at: string;
};

const tabs: { id: TabId; label: string }[] = [
  { id: "receipt", label: "Upload a receipt" },
  { id: "ingredients", label: "View ingredients" },
  { id: "meals", label: "Generate meal" },
  { id: "cookbook", label: "View cookbook" },
];

export function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("receipt");
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

  function handleLogout() {
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
          {activeTab === "receipt" && (
            <UploadReceiptTab onIngredientsChanged={refreshIngredients} />
          )}
          {activeTab === "ingredients" && (
            <IngredientsTab refreshKey={ingredientsRefreshKey} />
          )}
          {activeTab === "meals" && <GenerateMealTab />}
          {activeTab === "cookbook" && (
            <ListTab<CookbookEntry>
              title="Your cookbook"
              emptyMessage="No cookbook entries yet."
              endpoint="/api/cookbook"
              renderItem={(item) => (
                <div>
                  <p className="font-medium text-stone-900">{item.title}</p>
                  {item.ingredients && (
                    <p className="mt-1 text-sm text-stone-500">
                      {item.ingredients}
                    </p>
                  )}
                </div>
              )}
            />
          )}
        </section>
      </main>
    </div>
  );
}

function UploadReceiptTab({
  onIngredientsChanged,
}: {
  onIngredientsChanged: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [reviewReceipt, setReviewReceipt] = useState<Receipt | null>(null);
  const [savedIngredients, setSavedIngredients] = useState<Ingredient[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function loadReceipts() {
    setLoading(true);
    setError("");

    try {
      const response = await apiFetch("/api/receipts");
      if (!response.ok) {
        throw new Error(await parseError(response, "Unable to load receipts."));
      }

      const data: Receipt[] = await response.json();
      setReceipts(data);

      const pending = data.find(
        (receipt) => receipt.analysis_status === "pending_review",
      );
      if (pending && !reviewReceipt) {
        setReviewReceipt(pending);
      }
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load receipts.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadReceipts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleUpload() {
    if (!file) {
      setError("Choose a receipt photo to upload.");
      return;
    }

    setUploading(true);
    setError("");
    setMessage("");
    setReviewReceipt(null);
    setSavedIngredients([]);

    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("manual_items", "[]");

      const response = await apiFetch("/api/receipts/upload", {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (!response.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : "Upload failed.";
        throw new Error(detail);
      }

      setFile(null);
      setReviewReceipt(data);
      setMessage(
        `Ready to review ${data.draft_items.length} item${data.draft_items.length === 1 ? "" : "s"} from ${data.store_name ?? "your receipt"}.`,
      );
      await loadReceipts();
    } catch (uploadError) {
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : "Upload failed.",
      );
    } finally {
      setUploading(false);
    }
  }

  function handleConfirmed(ingredients: Ingredient[]) {
    setReviewReceipt(null);
    setSavedIngredients(ingredients);
    setMessage(
      `Saved ${ingredients.length} ingredient${ingredients.length === 1 ? "" : "s"} with nutrition facts.`,
    );
    onIngredientsChanged();
    loadReceipts();
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">Upload a receipt</h2>
        <p className="mt-1 text-sm text-stone-500">
          Add ingredients by hand and/or upload a receipt photo. Claude will read
          the receipt, then you can review everything together before saving.
        </p>
      </div>

      {!reviewReceipt && (
        <>
          <ManualIngredientList
            disabled={uploading}
            onIngredientAdded={() => onIngredientsChanged()}
          />

          <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50 p-6">
            <h3 className="text-sm font-semibold text-stone-900">
              Upload receipt photo
            </h3>
            <p className="mt-1 text-sm text-stone-500">
              Claude will read your receipt and add items for you to review before saving.
            </p>
            <input
              type="file"
              accept="image/*,.pdf"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="mt-4 block w-full text-sm text-stone-600 file:mr-4 file:rounded-lg file:border-0 file:bg-orange-500 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-orange-600"
            />
            <button
              type="button"
              onClick={handleUpload}
              disabled={uploading || !file}
              className="mt-4 rounded-xl bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {uploading ? "Analyzing receipt with AI..." : "Upload & analyze receipt"}
            </button>
          </div>
        </>
      )}

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

      {reviewReceipt && reviewReceipt.draft_items.length > 0 && (
        <ReceiptReview
          receiptId={reviewReceipt.id}
          storeName={reviewReceipt.store_name}
          initialItems={reviewReceipt.draft_items}
          onConfirmed={handleConfirmed}
          onCancel={() => {
            setReviewReceipt(null);
          }}
        />
      )}

      {savedIngredients.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
            Saved ingredients
          </h3>
          <ul className="space-y-3">
            {savedIngredients.map((ingredient) => (
              <li key={ingredient.id}>
                <IngredientCard ingredient={ingredient} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-stone-500">
          Uploaded receipts
        </h3>
        {loading ? (
          <p className="text-sm text-stone-500">Loading receipts...</p>
        ) : receipts.length === 0 ? (
          <p className="text-sm text-stone-500">No receipts uploaded yet.</p>
        ) : (
          <ul className="divide-y divide-stone-100 rounded-2xl border border-stone-200">
            {receipts.map((receipt) => {
              const itemCount =
                receipt.analysis_status === "pending_review"
                  ? receipt.draft_items.length
                  : receipt.ingredients.length;

              return (
                <li key={receipt.id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium text-stone-800">
                        {receipt.original_name}
                      </p>
                      <p className="text-xs text-stone-500">
                        {receipt.store_name ?? "Unknown store"} · {itemCount} item
                        {itemCount === 1 ? "" : "s"}
                        {receipt.analysis_status === "pending_review" &&
                          " · awaiting review"}
                      </p>
                      {receipt.analysis_status === "failed" &&
                        receipt.analysis_error && (
                          <p className="mt-1 text-xs text-red-600">
                            {receipt.analysis_error}
                          </p>
                        )}
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-2">
                      <span className="text-xs text-stone-400">
                        {new Date(receipt.uploaded_at).toLocaleString()}
                      </span>
                      {receipt.analysis_status === "pending_review" && (
                        <button
                          type="button"
                          onClick={() => setReviewReceipt(receipt)}
                          className="rounded-lg bg-orange-100 px-2.5 py-1 text-xs font-medium text-orange-700 transition hover:bg-orange-200"
                        >
                          Continue review
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function GenerateMealTab() {
  const [meals, setMeals] = useState<Meal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadMeals() {
      setLoading(true);
      setError("");

      try {
        const response = await apiFetch("/api/meals");
        if (!response.ok) {
          throw new Error(await parseError(response, "Unable to load meals."));
        }

        setMeals(await response.json());
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

    loadMeals();
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">Generate meal</h2>
        <p className="mt-1 text-sm text-stone-600">
          Create a meal suggestion from the ingredients in your kitchen.
        </p>
      </div>

      <button
        type="button"
        className="rounded-xl bg-orange-500 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600"
      >
        Generate meal
      </button>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-stone-500">Loading...</p>
      ) : meals.length > 0 ? (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-stone-800">Your meals</h3>
          <ul className="space-y-3">
            {meals.map((meal) => (
              <li
                key={meal.id}
                className="rounded-2xl border border-stone-200 bg-stone-50 p-4"
              >
                <p className="font-medium text-stone-900">{meal.name}</p>
                {meal.description && (
                  <p className="mt-1 text-sm text-stone-500">{meal.description}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function IngredientsTab({ refreshKey }: { refreshKey: number }) {
  const [items, setItems] = useState<Ingredient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [removingId, setRemovingId] = useState<string | null>(null);

  useEffect(() => {
    async function loadIngredients() {
      setLoading(true);
      setError("");

      try {
        const response = await apiFetch("/api/ingredients");
        if (!response.ok) {
          throw new Error(await parseError(response, "Unable to load ingredients."));
        }

        setItems(await response.json());
      } catch (loadError) {
        setError(
          loadError instanceof Error
            ? loadError.message
            : "Unable to load ingredients.",
        );
      } finally {
        setLoading(false);
      }
    }

    loadIngredients();
  }, [refreshKey]);

  async function removeIngredient(id: string) {
    setRemovingId(id);
    setError("");

    try {
      const response = await apiFetch(`/api/ingredients/${id}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        throw new Error(await parseError(response, "Unable to remove ingredient."));
      }

      setItems((current) => current.filter((item) => item.id !== id));
    } catch (removeError) {
      setError(
        removeError instanceof Error
          ? removeError.message
          : "Unable to remove ingredient.",
      );
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-stone-900">Your ingredients</h2>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-stone-500">Loading...</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-stone-500">
          No ingredients yet. Add one manually or upload a receipt.
        </p>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id}>
              <IngredientCard
                ingredient={item}
                onRemove={() => removeIngredient(item.id)}
                removing={removingId === item.id}
              />
            </li>
          ))}
        </ul>
      )}
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
