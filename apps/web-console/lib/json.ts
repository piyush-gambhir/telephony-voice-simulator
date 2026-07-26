// Shared, non-"use client" helpers so Server Components can import them
// without turning them into client references.

export function looksLikeJson(value: unknown): boolean {
  if (typeof value !== "string") return typeof value === "object" && value !== null;
  const t = value.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return false;
  try {
    JSON.parse(t);
    return true;
  } catch {
    return false;
  }
}
