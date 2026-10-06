import Link from "next/link";
import { getSession } from "@/lib/session";
import { apiGet } from "@/lib/api";
import type { CaseRead } from "@/lib/types";
import { CaseSwitcher } from "./case-switcher";

/**
 * Case switcher in the top bar.
 *
 * Only shown once a case exists — a new deployment with zero cases should show a
 * clean header, not an empty dropdown implying something is missing.
 *
 * A failure here is non-fatal on purpose: the shell must not fail to render
 * because the case list could not be fetched.
 */
export async function CaseNav() {
  const user = await getSession();
  if (!user) return null;

  let cases: CaseRead[] = [];
  try {
    cases = await apiGet<CaseRead[]>("/cases?limit=50");
  } catch {
    return null;
  }
  if (cases.length === 0) return null;

  const recent = cases
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 25);

  return (
    <nav aria-label="Cases" className="hidden sm:block">
      <CaseSwitcher cases={recent} />
      <Link
        href="/cases/new"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-accent focus:px-3 focus:py-1.5 focus:text-xs focus:font-medium focus:text-accent-fg"
      >
        New case
      </Link>
    </nav>
  );
}
