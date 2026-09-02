"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Field, PasswordInput } from "@/components/auth-fields";
import { resetPassword } from "@/lib/api";
import { PASSWORD_REQUIREMENTS, validatePassword } from "@/lib/password";

export function ResetPasswordForm() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setToken(new URLSearchParams(window.location.search).get("token") ?? "");
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (!token) {
      setError("This reset link is invalid or has expired.");
      return;
    }

    const passwordError = validatePassword(password);
    if (passwordError) {
      setError(passwordError);
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);

    try {
      await resetPassword(token, password);
      router.replace("/login");
      router.refresh();
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "Unable to reset your password.",
      );
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-md">
      <div className="mb-8 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-orange-500 text-2xl shadow-lg shadow-orange-500/30">
          🍽️
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-stone-900">
          Choose a new password
        </h1>
        <p className="mt-2 text-sm text-stone-500">
          Use a strong password you have not used here before.
        </p>
      </div>

      <div className="rounded-3xl border border-stone-200/80 bg-white/90 p-8 shadow-xl shadow-stone-900/5 backdrop-blur">
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="New password">
            <PasswordInput
              id="password"
              value={password}
              onChange={setPassword}
              showPassword={showPassword}
              onToggleVisibility={() => setShowPassword((visible) => !visible)}
              autoComplete="new-password"
              required
            />
            <ul className="mt-2 space-y-1 text-xs text-stone-500">
              {PASSWORD_REQUIREMENTS.map((requirement) => (
                <li key={requirement} className="flex items-center gap-2">
                  <span className="text-stone-300">•</span>
                  {requirement}
                </li>
              ))}
            </ul>
          </Field>

          <Field label="Confirm password">
            <PasswordInput
              id="confirm-password"
              value={confirmPassword}
              onChange={setConfirmPassword}
              showPassword={showPassword}
              onToggleVisibility={() => setShowPassword((visible) => !visible)}
              autoComplete="new-password"
              required
            />
          </Field>

          {error && (
            <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-xl bg-orange-500 px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-orange-500/25 transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Please wait..." : "Update password"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-stone-500">
          <Link
            href="/login"
            className="font-semibold text-orange-600 hover:text-orange-700"
          >
            Back to sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
