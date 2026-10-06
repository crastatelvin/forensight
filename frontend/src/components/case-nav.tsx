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
    /* No "New case" link here. There was one, `sr-only` until focused, which put
       two links with the same accessible name on the page — ambiguous for anyone
       navigating by link list, and it shadowed the real one for anything that
       resolves links by name. The case switcher is the nav's job; creating a case
       belongs on the page that lists them. */
    <nav aria-label="Cases" className="hidden sm:block">
      <CaseSwitcher cases={recent} />
    </nav>
  );
}
