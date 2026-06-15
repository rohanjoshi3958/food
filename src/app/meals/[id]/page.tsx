import type { Metadata } from "next";
import { MealProceedPage } from "@/components/meal-proceed-page";

export const metadata: Metadata = {
  title: "Food | Your meal",
  description: "View your meal and upload a photo.",
};

export default async function MealPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return <MealProceedPage mealId={id} />;
}
