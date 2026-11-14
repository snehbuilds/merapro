/*
 * Group identity colours. Groups are user-created, so their colours are
 * generated rather than named (docs/12 F1.2). Group colour is identity, never
 * status: it is only ever carried as a small square mark (GroupDot) next to
 * the group's name, so it does not need to meet text contrast, only the 3:1
 * non-text contrast against the surfaces it sits on.
 *
 * The palette
 *   index 0..2   the mockup seeds --g-1 / --g-2 / --g-3 (globals.css), which
 *                already have tuned light and dark values.
 *   index 3+     generated in OKLCH. Hue starts from the seed hues and steps by
 *                the golden angle (137.5°) so neighbouring indexes stay far apart
 *                however many groups exist. Lightness and chroma are fixed per
 *                theme to the seeds' band:
 *                  light  L 0.52  C 0.10   (>= 3:1 on --bg #f5f3ed and --bg-raised)
 *                  dark   L 0.76  C 0.10   (>= 3:1 on --bg #111315 and --bg-raised)
 *                The value is a CSS `light-dark()` pair, resolved by the
 *                `color-scheme` each theme already sets, so one string is safe
 *                in both themes and in a nested [data-theme] subtree.
 *
 * Keys
 *   groupColor(2)          a stable position (e.g. the group's sort order)
 *   groupColor("grp_01J…") an id; hashed (FNV-1a) to an index so the colour
 *                          stays the same wherever the group appears.
 */

export const SEED_GROUP_COLORS = ["var(--g-1)", "var(--g-2)", "var(--g-3)"] as const;

/** Approximate OKLCH hues of the seeds (#3d6596, #7b4f93, #a2661f). */
const SEED_HUE = 255;
const GOLDEN_ANGLE = 137.508;

export const GROUP_COLOR_BANDS = {
  light: { l: 0.52, c: 0.1 },
  dark: { l: 0.76, c: 0.1 },
} as const;

/** FNV-1a 32-bit; deterministic across server and client. */
export function hashString(value: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Palette index for a key. Strings hash into a 64-slot palette. */
export function groupColorIndex(key: number | string): number {
  if (typeof key === "number") return Math.max(0, Math.floor(key));
  return hashString(key) % 64;
}

/** Hue in degrees for a generated (index >= 3) colour. */
export function groupHue(index: number): number {
  const hue = (SEED_HUE + (index - 2) * GOLDEN_ANGLE) % 360;
  return Math.round(hue * 10) / 10;
}

/** A CSS colour for the group, safe in light and dark. */
export function groupColor(key: number | string): string {
  const index = groupColorIndex(key);
  const seed = SEED_GROUP_COLORS[index];
  if (seed) return seed;
  const h = groupHue(index);
  const { light, dark } = GROUP_COLOR_BANDS;
  return `light-dark(oklch(${light.l} ${light.c} ${h}), oklch(${dark.l} ${dark.c} ${h}))`;
}
