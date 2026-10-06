"use client";

import { useCallback, useSyncExternalStore } from "react";

type Theme = "light" | "dark";

const KEY = "forensight-theme";

/**
 * Persisted light/dark toggle.
 *
 * The `.dark` class is applied by the inline script in the layout before first
 * paint, so this component does not own it — it only owns the control and
 * persistence.
 *
 * The current theme is read with `useSyncExternalStore` rather than mirrored into
 * state from an effect. The theme genuinely *is* external state: the class is on
 * `documentElement` and the preference lives in `localStorage`, both of which
 * exist before React does. Copying that into `useState` in an effect means
 * rendering the wrong theme once and then correcting it, which is the cascading
 * render the linter is right to complain about. The server snapshot is "light"
 * because the server genuinely cannot know, and the inline script corrects the
 * DOM before it paints.
 */
function subscribe(onChange: () => void): () => void {
  // `storage` fires in *other* tabs; `matchMedia` fires when the OS preference
  // changes under us. Both change the resolved theme without a click here.
  window.addEventListener("storage", onChange);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    media.removeEventListener("change", onChange);
  };
}

function stored(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

function resolve(): Theme {
  if (typeof document === "undefined") return "light";
  // The DOM class is authoritative: the inline script set it before paint.
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export function ThemeInit() {
  const theme = useSyncExternalStore(subscribe, resolve, () => "light" as Theme);

  const toggle = useCallback(() => {
    const next: Theme = resolve() === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // Private browsing blocks writes. The toggle still works for this page
      // view; it just will not be remembered, which is not worth an error.
    }
  }, []);

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      className="fixed right-4 bottom-4 z-50 flex h-10 w-10 items-center justify-center rounded-full border border-border-default bg-surface-1 text-secondary shadow-lg transition-colors hover:bg-surface-2 hover:text-primary"
    >
      <span aria-hidden="true" className="text-base leading-none">
        {theme === "dark" ? "\u2600" : "\u263E"}
      </span>
    </button>
  );
}

export { KEY as THEME_STORAGE_KEY, stored, resolve };
