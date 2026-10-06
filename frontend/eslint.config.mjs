import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/**
 * ESLint flat config.
 *
 * `eslint-config-next` 16 is flat-native, so it is imported directly rather than
 * through `FlatCompat` — the compat shim exists for legacy eslintrc configs and
 * crashes on this one ("Converting circular structure to JSON").
 *
 * Almost nothing is added on top. Next's config already carries jsx-a11y, which
 * is where the WCAG 2.1 AA work is enforced rather than asserted in prose. The
 * only override is `no-explicit-any`: an analyzer's `payload` is genuinely
 * untyped JSON, and the modules narrow it honestly — typing it as `any` and
 * checking at the use site is more truthful than a cast that lies.
 */
const config = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        // `_`-prefixed bindings are the convention for "deliberately unused".
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;
