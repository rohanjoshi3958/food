import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { auth, signOut } from "@/auth";

export const metadata: Metadata = {
  title: "Food | Home",
  description: "Your food favorites, all in one place.",
};

export default async function Home() {
  const session = await auth();

  if (!session?.user) {
    redirect("/login");
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center bg-stone-50 px-4 py-16">
      <div className="w-full max-w-lg rounded-3xl border border-stone-200 bg-white p-10 text-center shadow-xl shadow-stone-900/5">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-orange-500 text-3xl shadow-lg shadow-orange-500/30">
          🍽️
        </div>
        <h1 className="text-2xl font-bold text-stone-900">
          Welcome{session.user.name ? `, ${session.user.name}` : ""}!
        </h1>
        <p className="mt-2 text-stone-500">
          Signed in as{" "}
          <span className="font-medium text-stone-700">
            {session.user.email}
          </span>
        </p>

        <form
          action={async () => {
            "use server";
            await signOut({ redirectTo: "/login" });
          }}
          className="mt-8"
        >
          <button
            type="submit"
            className="rounded-xl border border-stone-200 px-5 py-2.5 text-sm font-medium text-stone-700 transition hover:bg-stone-50"
          >
            Sign out
          </button>
        </form>
      </div>
    </div>
  );
}
