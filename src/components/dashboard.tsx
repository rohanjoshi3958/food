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

type TabId = "receipt" | "ingredients" | "meals" | "cookbook";

type Receipt = {
  id: string;
  original_name: string;
  uploaded_at: string;
};

type Ingredient = {
  id: string;
  name: string;
  quantity: string | null;
  unit: string | null;
  created_at: string;
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
  { id: "meals", label: "View meals" },
  { id: "cookbook", label: "View cookbook" },
];

export function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("receipt");
  const [loading, setLoading] = useState(true);

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
          {activeTab === "receipt" && <UploadReceiptTab />}
          {activeTab === "ingredients" && (
            <ListTab<Ingredient>
              title="Your ingredients"
              emptyMessage="No ingredients yet. Upload a receipt to get started."
              endpoint="/api/ingredients"
              renderItem={(item) => (
                <div>
                  <p className="font-medium text-stone-900">{item.name}</p>
                  {(item.quantity || item.unit) && (
                    <p className="text-sm text-stone-500">
                      {[item.quantity, item.unit].filter(Boolean).join(" ")}
                    </p>
                  )}
                </div>
              )}
            />
          )}
          {activeTab === "meals" && (
            <ListTab<Meal>
              title="Your meals"
              emptyMessage="No meals yet."
              endpoint="/api/meals"
              renderItem={(item) => (
                <div>
                  <p className="font-medium text-stone-900">{item.name}</p>
                  {item.description && (
                    <p className="text-sm text-stone-500">{item.description}</p>
                  )}
                </div>
              )}
            />
          )}
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

function UploadReceiptTab() {
  const [file, setFile] = useState<File | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
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

      setReceipts(await response.json());
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
  }, []);

  async function handleUpload() {
    if (!file) {
      setError("Choose a receipt file to upload.");
      return;
    }

    setUploading(true);
    setError("");
    setMessage("");

    try {
      const formData = new FormData();
      formData.append("file", file);

      const response = await apiFetch("/api/receipts/upload", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(await parseError(response, "Upload failed."));
      }

      setFile(null);
      setMessage("Receipt uploaded successfully.");
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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">Upload a receipt</h2>
        <p className="mt-1 text-sm text-stone-500">
          Upload a photo or PDF of your grocery receipt.
        </p>
      </div>

      <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50 p-6">
        <input
          type="file"
          accept="image/*,.pdf"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          className="block w-full text-sm text-stone-600 file:mr-4 file:rounded-lg file:border-0 file:bg-orange-500 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-orange-600"
        />
        <button
          type="button"
          onClick={handleUpload}
          disabled={uploading || !file}
          className="mt-4 rounded-xl bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {uploading ? "Uploading..." : "Upload receipt"}
        </button>
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
            {receipts.map((receipt) => (
              <li
                key={receipt.id}
                className="flex items-center justify-between px-4 py-3"
              >
                <span className="text-sm font-medium text-stone-800">
                  {receipt.original_name}
                </span>
                <span className="text-xs text-stone-400">
                  {new Date(receipt.uploaded_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ListTab<T extends { id: string }>({
  title,
  emptyMessage,
  endpoint,
  renderItem,
}: {
  title: string;
  emptyMessage: string;
  endpoint: string;
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
  }, [endpoint]);

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
        <ul className="divide-y divide-stone-100 rounded-2xl border border-stone-200">
          {items.map((item) => (
            <li key={item.id} className="px-4 py-3">
              {renderItem(item)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
