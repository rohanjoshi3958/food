"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  AuthUser,
  getCurrentUser,
  getToken,
  logout,
} from "@/lib/api";
import { CookbookTab } from "@/components/cookbook-tab";
import { GenerateMealTab } from "@/components/generate-meal-tab";
import { IngredientsTab } from "@/components/ingredients-tab";
import { ReceiptUploadTab } from "@/components/receipt-upload-tab";

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
            <GenerateMealTab
              refreshKey={ingredientsRefreshKey}
              onIngredientsChanged={refreshIngredients}
              onViewCookbook={() => setActiveTab("cookbook")}
            />
          </div>
          <div className={activeTab === "cookbook" ? "block" : "hidden"}>
            <CookbookTab />
          </div>
        </section>
      </main>
    </div>
  );
}
