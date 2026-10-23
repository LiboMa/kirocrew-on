import { useSyncExternalStore } from "react";

/** Light or dark (owner 2026-10-24, 青岩 with a dark mode): the person's choice is kept in this browser; before any choice
 * the system's setting decides. Applied as the ``dark`` class on <html> (index.css), before the first render. */
export type Theme = "light" | "dark";
const KEY = "octopus.theme";
const listeners = new Set<() => void>();

function stored(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function currentTheme(): Theme {
  return stored() ?? (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

export function applyTheme(theme: Theme = currentTheme()) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function setTheme(theme: Theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // a private window without storage: this tab only
  }
  applyTheme(theme);
  listeners.forEach((listener) => listener());
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore((listener) => { listeners.add(listener); return () => listeners.delete(listener); }, currentTheme,
    () => "light" as Theme);
  return [theme, setTheme];
}
