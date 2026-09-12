import type { Metadata } from "next";
import { LlmUsageDashboard } from "@/components/llm-usage-dashboard";

export const metadata: Metadata = {
  title: "Food | Claude usage & cost",
  description: "Internal per-workflow Claude token and cost dashboard.",
  robots: { index: false, follow: false },
};

export default function LlmUsagePage() {
  return <LlmUsageDashboard />;
}
