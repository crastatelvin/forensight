import type { ReactNode } from "react";

/**
 * The shared primitives.
 *
 * Deliberately a handful of plain elements rather than a component library. Every
 * one here is a thin wrapper that fixes a class or a semantics problem; nothing
 * adds behaviour that could not be written inline more clearly. If a future
 * primitive needs state, animation or a portal, it earns a real dependency — this
 * set deliberately does not anticipate that.
 *
 * Every interactive element carries its own focus-visible ring from `globals.css`,
 * so keyboard users can see where they are without a component having to remember.
 */

function cx(...parts: Array<string | false | null | undefined | 0>): string {
  // `0` is allowed because `title && "..."` in the Notice header is the idiomatic
  // way to express "no title", and a ReactNode can legitimately render as 0.
  return parts
    .filter((p): p is string => typeof p === "string" && p.length > 0)
    .join(" ");
}

// --- Card ---------------------------------------------------------------

export function Card({
  children,
  className,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "section" | "article";
}) {
  return (
    <Tag
      className={cx("rounded-lg border border-border-subtle bg-surface-1", className)}
    >
      {children}
    </Tag>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
  id,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  id?: string;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle px-4 py-3">
      <div className="min-w-0">
        <h2 id={id} className="text-sm font-semibold tracking-tight">
          {title}
        </h2>
        {subtitle ? <p className="mt-0.5 text-xs text-secondary">{subtitle}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function CardBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cx("p-4", className)}>{children}</div>;
}

// --- Button -------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "success";
type ButtonSize = "sm" | "md";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent-hover border-transparent",
  secondary: "bg-surface-inset text-primary hover:bg-surface-2 border-border-default",
  ghost: "bg-transparent text-secondary hover:bg-surface-2 border-transparent",
  danger:
    "bg-alert-bg text-alert hover:brightness-95 border-alert-border dark:hover:brightness-110",
  success:
    "bg-calm-bg text-calm hover:brightness-95 border-calm-border dark:hover:brightness-110",
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
};

/**
 * A button.
 *
 * `type="button"` is set by default because the HTML default is `submit`, and an
 * untyped button inside a form submits it. That is a data-loss bug, not a
 * cosmetic one, so the safe default is baked in rather than left to the caller.
 */
export function Button({
  children,
  variant = "secondary",
  size = "md",
  className,
  type = "button",
  ...rest
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
  type?: "button" | "submit";
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type" | "className">) {
  return (
    <button
      type={type}
      className={cx(
        "inline-flex shrink-0 items-center justify-center rounded-md border font-medium transition-colors",
        "disabled:pointer-events-none disabled:opacity-50",
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

// --- Badge --------------------------------------------------------------

type Tone = "neutral" | "accent" | "alert" | "warn" | "calm" | "info";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-secondary border-border-default",
  accent: "bg-accent-subtle text-accent border-accent-border",
  alert: "bg-alert-bg text-alert border-alert-border",
  warn: "bg-warn-bg text-warn border-warn-border",
  calm: "bg-calm-bg text-calm border-calm-border",
  info: "bg-info-bg text-info border-info-border",
};

export function Badge({
  children,
  tone = "neutral",
  className,
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

// --- Empty state --------------------------------------------------------

/**
 * The empty state.
 *
 * Every list in the app uses this rather than rendering nothing, because an empty
 * area with no explanation reads as a failed load. The optional `hint` is where the
 * empty state earns its keep: it says what would fill this and how.
 */
export function EmptyState({
  title,
  hint,
  action,
  icon = "—",
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <div
        aria-hidden="true"
        className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-2 text-lg text-tertiary"
      >
        {icon}
      </div>
      <p className="text-sm font-medium">{title}</p>
      {hint ? (
        <p className="max-w-md text-xs leading-relaxed text-secondary">{hint}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

// --- Skeleton -----------------------------------------------------------

/**
 * A loading placeholder.
 *
 * `aria-hidden` plus the live region in the parent means a screen reader hears
 * "loading" once rather than hearing a dozen shape descriptions.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cx("animate-pulse rounded-md bg-surface-2", className)}
    />
  );
}

/** A card-shaped skeleton, used while a server component fetches. */
export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <Card className="p-4">
      <div className="space-y-2.5">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton key={i} className={i === 0 ? "h-4 w-1/3" : "h-3 w-full"} />
        ))}
      </div>
    </Card>
  );
}

// --- Alert / notice -----------------------------------------------------

/**
 * A notice box. `tone="warn"` and `"alert"` are not decoration — they are how the
 * honesty clause reaches the user. A disclaimer the reader can miss is not a
 * disclaimer.
 */
export function Notice({
  tone = "info",
  title,
  children,
  icon,
  className,
}: {
  tone?: Tone;
  title?: ReactNode;
  children: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  const icons: Record<Tone, ReactNode> = {
    neutral: "•",
    accent: "•",
    alert: "!",
    warn: "!",
    calm: "✓",
    info: "i",
  };
  return (
    <div
      className={cx(
        "flex gap-2.5 rounded-md border px-3 py-2.5 text-xs leading-relaxed",
        TONES[tone],
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-current text-[10px] font-bold"
      >
        {icon ?? icons[tone]}
      </span>
      <div className="min-w-0 flex-1">
        {title ? <div className="font-semibold">{title}</div> : null}
        <div className={title ? "mt-0.5" : undefined}>{children}</div>
      </div>
    </div>
  );
}

// --- Definition list ----------------------------------------------------

/**
 * A key/value grid.
 *
 * `<dl>` because these are genuinely term/definition pairs — a table would be
 * read as tabular data with headers, which is wrong.
 */
export function DefList({
  items,
  columns = 2,
}: {
  items: Array<{ term: ReactNode; value: ReactNode }>;
  columns?: 1 | 2 | 3;
}) {
  const cols =
    columns === 1
      ? "sm:grid-cols-1"
      : columns === 3
        ? "sm:grid-cols-3"
        : "sm:grid-cols-2";
  return (
    <dl className={cx("grid grid-cols-1 gap-x-6 gap-y-2.5", cols)}>
      {items.map((item, i) => (
        <div key={i} className="min-w-0">
          <dt className="text-[11px] font-medium uppercase tracking-wide text-tertiary">
            {item.term}
          </dt>
          <dd className="mt-0.5 break-words text-xs text-primary">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

// --- Progress -----------------------------------------------------------

/**
 * An indeterminate progress bar for a queued or running job.
 *
 * Purely decorative (`role="presentation"`); the status text beside it is what a
 * screen reader is told. An animated bar marked `role="progressbar"` without a
 * determinate value would announce garbage.
 */
export function IndeterminateBar({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <div
        role="presentation"
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2"
      >
        <div className="h-full w-1/3 animate-pulse rounded-full bg-accent" />
      </div>
      {label ? (
        <span className="shrink-0 text-[11px] text-secondary">{label}</span>
      ) : null}
    </div>
  );
}

// --- Helpers ------------------------------------------------------------

export { cx };
