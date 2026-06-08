import type { Metadata } from "next";
import { Dashboard } from "@/components/dashboard";

export const metadata: Metadata = {
  title: "Food | Dashboard",
  description: "Manage receipts, ingredients, meals, and your cookbook.",
};

export default function Home() {
  return <Dashboard />;
}
