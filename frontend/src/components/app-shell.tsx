import Link from "next/link";
import { getSession } from "@/lib/session";
import { SignOutButton } from "@/components/sign-out-button";
import { CaseNav } from "@/components/case-nav";

/**
 * Authenticated chrome: top bar, case switcher, main region.
 *
 * Rendered in the root layout so navigation does not remount on navigation — but
 * the login page uses its own bare layout, so this has to tolerate having no
 * session rather than assume one.
 */
export async function AppShell({ children }: { children: React.ReactNode }) {
  const user = await getSession();

  if (!user) return <>{children}</>;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-border-subtle bg-surface-1/85 backdrop-blur-sm">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-2.5 sm:px-6">
          <Link
            href="/"
            className="flex shrink-0 items-center gap-2 font-semibold tracking-tight"
          >
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-sm font-bold text-accent-fg"
            >
              F
            </span>
            <span className="hidden sm:inline">ForenSight</span>
          </Link>

          <CaseNav />

          <div className="ml-auto flex items-center gap-3">
            <div className="text-right leading-tight">
              <div className="text-xs font-medium">{user.email}</div>
              <div className="text-[11px] text-tertiary uppercase tracking-wide">
                {user.role}
              </div>
            </div>
            <SignOutButton />
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
        {children}
      </main>

      <footer className="border-t border-border-subtle px-4 py-4 text-center text-xs text-tertiary sm:px-6">
        Every result carries its threshold, model version and limitations. A score alone
        is not a finding.
      </footer>
    </div>
  );
}
