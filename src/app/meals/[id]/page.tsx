import { redirect } from "next/navigation";

export default async function MealPage() {
  redirect("/?tab=meals");
}
