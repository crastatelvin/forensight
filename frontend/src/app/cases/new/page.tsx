import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { requireSession } from "@/lib/session";
import { NewCaseForm } from "./new-case-form";

export const metadata: Metadata = { title: "New case" };

export default async function NewCasePage() {
  const user = await requireSession();
  // Reviewers are read-only in the backend's RBAC; offering them a form that
  // always 403s would be a worse experience than saying so here.
  if (user.role === "reviewer") redirect("/");

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">New case</h1>
        <p className="mt-1 text-sm text-secondary">
          The case number must be unique and is what everything else references.
        </p>
      </header>
      <NewCaseForm />
    </div>
  );
}
