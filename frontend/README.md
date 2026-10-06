# frontend/ — ForenSight web client

Next.js App Router + TypeScript + Tailwind v4. Serves the case workspace, the
streaming agent console and the evidence viewer.

```bash
npm ci
npm run dev            # http://localhost:3000
npm run check          # typecheck + lint + build — what CI runs
npm test               # journey checks against a live stack
```

Set `FORENSIGHT_API_URL` to point at a backend (defaults to
`http://localhost:8000`). For local development over plain HTTP, also set
`FORENSIGHT_INSECURE_COOKIES=1` — without it the session cookie is marked
`Secure` and the browser drops it over `http://localhost`, which presents as
"logged in but bounced straight back to the login page".

## How it talks to the API

Every request goes through Next's server. The browser never calls the API
directly, for three reasons that are load-bearing rather than stylistic:

1. The access token is an **HttpOnly** cookie, so JavaScript cannot read it. A
   browser-side `fetch` to the API would be unauthenticated.
2. CORS and CSRF stop being two problems to solve.
3. API error shapes stop being the API's business to leak to a browser.

The cost is that data reads are server renders and live interaction goes through
a route handler under `src/app/api/`. For a case-management tool where
auditability beats SPA snappiness, that is the right trade.

`src/app/api/evidence/[evidenceId]/download` and `.../ela` exist for binary
content the browser must display. The ELA overlay is a `GET`, proxied because the
`Range` header the viewer uses to probe availability is also how it distinguishes
"this file's bytes are missing" (410) from "this file is an image that will not
decode".

## The honesty clause lives in one file

`src/lib/explain.ts`. `explainJob` is total: no input makes it return a score when
nothing was measured, so a `not_applicable` job cannot reach the UI as a zero.
That is the whole point — an absence of measurement is not a result, and a UI that
cannot express that difference will eventually stop trying.

## Not shadcn/ui

The plan named it; `src/components/ui.tsx` is a deliberate replacement. The app
needs nine primitives, most with no behaviour to share. See `CHECKLIST.md` P7.B1
for the reasoning and for when to flip the decision.
