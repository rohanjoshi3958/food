"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { Field, inputClassName } from "@/components/auth-fields";
import { requestPasswordReset } from "@/lib/api";

export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setLoading(true);

    try {
      const nextMessage = await requestPasswordReset(email);
      setMessage(nextMessage);
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "Unable to request a password reset.",
      );
    } finally {
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
          Reset your password
        </h1>
        <p className="mt-2 text-sm text-stone-500">
          Enter your email and we will send reset instructions if an account
          exists.
        </p>
      </div>

      <div className="rounded-3xl border border-stone-200/80 bg-white/90 p-8 shadow-xl shadow-stone-900/5 backdrop-blur">
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Email">
            <input
              id="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              required
              className={inputClassName}
            />
          </Field>

          {error && (
            <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">
              {error}
            </p>
          )}

          {message && (
            <p className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
              {message}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-xl bg-orange-500 px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-orange-500/25 transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Please wait..." : "Send reset instructions"}
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
