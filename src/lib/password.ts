export const PASSWORD_REQUIREMENTS = [
  "At least 8 characters",
  "At most 72 characters",
  "At least one uppercase letter",
  "At least one number",
  "At least one symbol",
] as const;

export function validatePassword(password: string): string | null {
  if (password.length < 8) {
    return "Password must be at least 8 characters.";
  }

  if (password.length > 72) {
    return "Password must be at most 72 characters.";
  }

  if (!/[A-Z]/.test(password)) {
    return "Password must include at least one uppercase letter.";
  }

  if (!/[0-9]/.test(password)) {
    return "Password must include at least one number.";
  }

  if (!/[^A-Za-z0-9]/.test(password)) {
    return "Password must include at least one symbol.";
  }

  return null;
}
