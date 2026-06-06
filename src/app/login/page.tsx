import type { Metadata } from "next";
import { AuthForm } from "@/components/auth-form";

export const metadata: Metadata = {
  title: "Food | Sign in",
  description: "Sign in or create an account with your email.",
};

export default function LoginPage() {
  return (
    <div className="relative flex min-h-full flex-1 items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_top,_#fff7ed,_#fafaf9_45%,_#f5f5f4)] px-4 py-12">
      <div className="pointer-events-none absolute -left-20 top-10 h-72 w-72 rounded-full bg-orange-200/40 blur-3xl" />
      <div className="pointer-events-none absolute -right-16 bottom-0 h-80 w-80 rounded-full bg-amber-200/30 blur-3xl" />
      <AuthForm />
    </div>
  );
}
