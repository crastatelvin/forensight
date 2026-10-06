import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getSession } from "@/lib/session";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  // Next 16 delivers searchParams as a promise.
  searchParams: Promise<{ from?: string }>;
}) {
  if (await getSession()) redirect("/");
  const { from } = await searchParams;

  return (
    <main
      id="main"
      className="flex min-h-screen items-center justify-center px-4 py-12"
    >
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mb-4 flex items-center justify-center gap-2.5">
            <span
              aria-hidden="true"
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-lg font-bold text-accent-fg"
            >
              F
            </span>
            <span className="text-xl font-semibold tracking-tight">ForenSight</span>
          </div>
          <p className="text-sm text-secondary">Digital forensic case management</p>
        </div>
        <LoginForm from={from ?? "/"} />
        <p className="mt-6 text-center text-xs leading-relaxed text-tertiary">
          Accounts are created by an administrator. There are no default credentials.
        </p>
      </div>
    </main>
  );
}
