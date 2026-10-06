/**
 * Contrast, computed from the tokens.
 *
 * The `--text-tertiary` token shipped at 4.07:1 against the nested surface while a
 * comment in `globals.css` claimed it was 4.6:1. axe-core caught it; nothing in
 * the build did, and nothing would have until the axe suite was written. A comment
 * asserting a number is not a check on that number.
 *
 * So this reads the stylesheet and computes the ratios. It fails on any pair that
 * drops below 4.5:1, and it fails if a token is missing — an unparsed token would
 * otherwise be silently skipped and the check would report "clean" over a palette
 * it never actually looked at.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

type RGB = [number, number, number];

const CSS = readFileSync(join(process.cwd(), "src", "app", "globals.css"), "utf8");

/** OKLCH -> sRGB, per the CSS Color 4 conversion. */
function oklchToSrgb(L: number, C: number, H: number): RGB {
  const h = (H * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;
  const encode = (c: number) => {
    const v = Math.max(0, Math.min(1, c));
    return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
  };
  return [
    encode(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    encode(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    encode(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

function relativeLuminance([r, g, b]: RGB): number {
  const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a: RGB, b: RGB): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Every `--token: oklch(...)` in a block. */
function parse(block: string): Map<string, RGB> {
  const out = new Map<string, RGB>();
  const re = /--([a-z0-9-]+):\s*oklch\(\s*([\d.]+)%?\s+([\d.]+)\s+([\d.]+)\s*\)/g;
  for (const m of block.matchAll(re)) {
    out.set(m[1], oklchToSrgb(Number(m[2]) / 100, Number(m[3]), Number(m[4])));
  }
  return out;
}

const LIGHT = parse(CSS.split(":root {")[1].split(".dark {")[0]);
const DARK = parse(CSS.split(".dark {")[1]);

/** Every foreground/background pair that actually occurs in the UI. */
const PAIRS: Array<[string, string, string]> = [
  // Body and secondary text on each surface it can land on.
  ["text-primary", "surface-0", "body on page"],
  ["text-primary", "surface-1", "body on card"],
  ["text-primary", "surface-2", "body on nested surface"],
  ["text-primary", "surface-inset", "body on input"],
  ["text-secondary", "surface-0", "secondary on page"],
  ["text-secondary", "surface-1", "secondary on card"],
  ["text-secondary", "surface-2", "secondary on nested surface"],
  ["text-tertiary", "surface-0", "tertiary on page"],
  ["text-tertiary", "surface-1", "tertiary on card"],
  ["text-tertiary", "surface-2", "tertiary on nested surface"],
  // Badge and notice text on their own tinted background.
  ["alert", "alert-bg", "alert badge"],
  ["warn", "warn-bg", "warn badge"],
  ["calm", "calm-bg", "calm badge"],
  ["info", "info-bg", "info badge"],
  ["accent", "accent-subtle", "accent badge"],
  // The theme toggle's own foreground on its own background.
  ["text-secondary", "surface-1", "toggle label"],
];

const FLOOR = 4.5;

for (const [label, palette] of [
  ["light", LIGHT],
  ["dark", DARK],
] as const) {
  test(`every ${label}-mode text pair clears ${FLOOR}:1`, () => {
    assert.ok(
      palette.size > 20,
      `only ${palette.size} tokens parsed — check the pattern`,
    );

    const failures: string[] = [];
    for (const [fg, bg, what] of PAIRS) {
      const f = palette.get(fg);
      const b = palette.get(bg);
      // A missing token must fail, not skip: silently skipping is how this class
      // of check reports "clean" over a palette it never examined.
      assert.ok(f, `${label}: --${fg} is missing or unparseable`);
      assert.ok(b, `${label}: --${bg} is missing or unparseable`);
      const ratio = contrast(f, b);
      if (ratio < FLOOR) {
        failures.push(
          `${what} (--${fg} on --${bg}): ${ratio.toFixed(2)}:1, needs ${FLOOR}:1`,
        );
      }
    }
    assert.equal(failures.join("\n"), "", `low contrast:\n${failures.join("\n")}`);
  });
}

test("dark mode redefines every token light mode defines", () => {
  // Otherwise a token silently keeps its light value on a dark surface, which is
  // the characteristic way a dark mode rots.
  const missing = [...LIGHT.keys()].filter((k) => !DARK.has(k));
  assert.deepEqual(missing, [], `dark mode is missing: ${missing.join(", ")}`);
});

test("the tertiary token has headroom, not a 4.5:1 hairline", () => {
  // It sits at exactly 4.5:1 against one surface by construction, which leaves no
  // margin for the sub-pixel antialiasing that makes small grey text look lighter
  // than it measures. A little more is cheap; a failure is not.
  const f = LIGHT.get("text-tertiary")!;
  const b = LIGHT.get("surface-2")!;
  assert.ok(contrast(f, b) >= 4.55, `only ${contrast(f, b).toFixed(2)}:1`);
});
