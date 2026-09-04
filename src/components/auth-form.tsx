"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Field, PasswordInput, inputClassName } from "@/components/auth-fields";
import { PASSWORD_REQUIREMENTS, validatePassword } from "@/lib/password";
import { getCurrentUser, login, register } from "@/lib/api";

type Mode = "signin" | "signup";

export function AuthForm() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getCurrentUser().then((user) => {
      if (user) {
        router.replace("/");
      }
    });
  }, [router]);

  function switchMode(nextMode: Mode) {
    setMode(nextMode);
    setError("");
    setConfirmPassword("");
    setShowPassword(false);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);

    try {
      if (mode === "signup") {
        const passwordError = validatePassword(password);

        if (passwordError) {
          setError(passwordError);
          setLoading(false);
          return;
        }

        if (password !== confirmPassword) {
          setError("Passwords do not match.");
          setLoading(false);
          return;
        }

        await register(name, email, password);
      } else {
        await login(email, password);
      }

      router.push("/");
      router.refresh();
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "Something went wrong. Please try again.",
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
          {mode === "signin" ? "Welcome back" : "Create your account"}
        </h1>
        <p className="mt-2 text-sm text-stone-500">
          {mode === "signin"
            ? "Sign in to discover and save your favorite meals."
            : "Join to track recipes, orders, and food favorites."}
        </p>
      </div>

      <div className="rounded-3xl border border-stone-200/80 bg-white/90 p-8 shadow-xl shadow-stone-900/5 backdrop-blur">
        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === "signup" && (
            <Field label="Name">
              <input
                id="name"
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Alex Johnson"
                autoComplete="name"
                className={inputClassName}
              />
            </Field>
          )}

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

          <Field label="Password">
            <PasswordInput
              id="password"
              value={password}
              onChange={setPassword}
              showPassword={showPassword}
              onToggleVisibility={() => setShowPassword((visible) => !visible)}
              autoComplete={
                mode === "signup" ? "new-password" : "current-password"
              }
              required
            />
            {mode === "signup" && (
              <ul className="mt-2 space-y-1 text-xs text-stone-500">
                {PASSWORD_REQUIREMENTS.map((requirement) => (
                  <li key={requirement} className="flex items-center gap-2">
                    <span className="text-stone-300">•</span>
                    {requirement}
                  </li>
                ))}
              </ul>
            )}
          </Field>

          {mode === "signup" && (
            <Field label="Confirm password">
              <PasswordInput
                id="confirm-password"
                value={confirmPassword}
                onChange={setConfirmPassword}
                showPassword={showPassword}
                onToggleVisibility={() =>
                  setShowPassword((visible) => !visible)
                }
                autoComplete="new-password"
                required
              />
            </Field>
          )}

          {mode === "signin" && (
            <div className="flex justify-end">
              <Link
                href="/forgot-password"
                className="text-sm font-medium text-orange-600 hover:text-orange-700"
              >
                Forgot password?
              </Link>
            </div>
          )}

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
            {loading
              ? "Please wait..."
              : mode === "signin"
                ? "Sign in"
                : "Create account"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-stone-500">
          {mode === "signin" ? (
            <>
              Don&apos;t have an account?{" "}
              <button
                type="button"
                onClick={() => switchMode("signup")}
                className="font-semibold text-orange-600 hover:text-orange-700"
              >
                Sign up
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button
                type="button"
                onClick={() => switchMode("signin")}
                className="font-semibold text-orange-600 hover:text-orange-700"
              >
                Sign in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
