"use client";

import { useRouter, usePathname } from "next/navigation";
import { useRef, useEffect, useId, useState } from "react";
import type { CaseRead } from "@/lib/types";
import { cx } from "@/components/ui";

/**
 * Case dropdown.
 *
 * A `<details>`/`<summary>` would be less JavaScript, but it cannot be styled to
 * close on outside-click or Escape reliably across browsers, and an open dropdown
 * that traps focus is an accessibility failure. So: a button plus a menu with
 * explicit keyboard handling.
 *
 * Arrow keys move, Escape closes and returns focus to the trigger, Home/End jump.
 */
export function CaseSwitcher({ cases }: { cases: CaseRead[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listId = useId();

  const current = cases.find((c) => pathname.startsWith(`/cases/${c.id}`));

  // Outside click and Escape. Registered only while open so a stray Escape on a
  // form field elsewhere in the app does not close a menu that was never opened.
  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function onTriggerKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
      setActive(
        Math.max(
          0,
          cases.findIndex((c) => c.id === current?.id),
        ),
      );
    }
  }

  function onMenuKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % cases.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i - 1 + cases.length) % cases.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActive(cases.length - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const chosen = cases[active];
      if (chosen) {
        setOpen(false);
        router.push(`/cases/${chosen.id}`);
      }
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        className="flex max-w-64 items-center gap-1.5 rounded-md border border-border-default bg-surface-inset px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-surface-2"
      >
        <span className="truncate">{current ? current.number : "All cases"}</span>
        <span aria-hidden="true" className="text-tertiary">
          ▾
        </span>
      </button>

      {open ? (
        <div
          role="listbox"
          id={listId}
          aria-label="Switch case"
          tabIndex={-1}
          onKeyDown={onMenuKeyDown}
          className="absolute left-0 top-full z-50 mt-1 max-h-80 w-72 overflow-auto rounded-md border border-border-default bg-surface-1 p-1 shadow-xl"
        >
          {cases.map((c, i) => (
            <button
              key={c.id}
              role="option"
              aria-selected={c.id === current?.id}
              onClick={() => {
                setOpen(false);
                router.push(`/cases/${c.id}`);
              }}
              onMouseEnter={() => setActive(i)}
              className={cx(
                "flex w-full flex-col items-start gap-0.5 rounded px-2.5 py-2 text-left",
                i === active ? "bg-accent-subtle" : "hover:bg-surface-2",
              )}
            >
              <span className="flex w-full items-center gap-2">
                <span className="font-mono text-xs font-medium">{c.number}</span>
                <span className="truncate text-[11px] text-tertiary">{c.status}</span>
              </span>
              <span className="truncate text-xs text-secondary">{c.title}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
