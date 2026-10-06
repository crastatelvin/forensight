import type { ReactNode } from "react";

/**
 * Status and priority pills.
 *
 * Colour is never the only signal: every pill carries its own text. That is a WCAG
 * requirement for more than aesthetics — a reviewer who is red-green colour blind
 * must be able to read the case status, and in a forensic tool that is a
 * correctness issue rather than an accessibility preference.
 */

const STATUS_TONES: Record<string, string> = {
  intake: "border-info-border bg-info-bg text-info",
  triage: "border-warn-border bg-warn-bg text-warn",
  investigation: "border-accent-border bg-accent-subtle text-accent",
  review: "border-warn-border bg-warn-bg text-warn",
  approved: "border-calm-border bg-calm-bg text-calm",
  rejected: "border-alert-border bg-alert-bg text-alert",
  closed: "border-border-default bg-surface-2 text-secondary",
  archived: "border-border-default bg-surface-2 text-tertiary",
};

export function StatusPill({ status }: { status: string }) {
  const key = status.toLowerCase();
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ${
        STATUS_TONES[key] ?? "border-border-default bg-surface-2 text-secondary"
      }`}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}

const PRIORITY_TONES: Record<string, string> = {
  low: "border-border-default bg-surface-2 text-secondary",
  normal: "border-border-default bg-surface-2 text-secondary",
  medium: "border-info-border bg-info-bg text-info",
  high: "border-warn-border bg-warn-bg text-warn",
  critical: "border-alert-border bg-alert-bg text-alert",
  urgent: "border-alert-border bg-alert-bg text-alert",
};

export function PriorityPill({ priority }: { priority: string }) {
  const key = priority.toLowerCase();
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ${
        PRIORITY_TONES[key] ?? "border-border-default bg-surface-2 text-secondary"
      }`}
    >
      {priority}
    </span>
  );
}

/** A role label. Roles gate actions, so they are named rather than implied. */
export function RolePill({ role }: { role: string }) {
  const tones: Record<string, string> = {
    admin: "border-alert-border bg-alert-bg text-alert",
    officer: "border-accent-border bg-accent-subtle text-accent",
    expert: "border-info-border bg-info-bg text-info",
    reviewer: "border-border-default bg-surface-2 text-secondary",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium capitalize ${
        tones[role] ?? "border-border-default bg-surface-2 text-secondary"
      }`}
    >
      {role}
    </span>
  );
}

/** A generic key/value pill, for tags on entities. */
export function Tag({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  const tones: Record<string, string> = {
    neutral: "border-border-default bg-surface-2 text-secondary",
    accent: "border-accent-border bg-accent-subtle text-accent",
    alert: "border-alert-border bg-alert-bg text-alert",
  };
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] ${
        tones[tone] ?? tones.neutral
      }`}
    >
      {children}
    </span>
  );
}
