/*
 * Grapheme counting for character limits. A user-perceived character is a
 * grapheme cluster: "👍🏽", "🇳🇿", "é" and "👩‍💻" each count as 1, as the
 * platforms count them, not as UTF-16 code units (String#length).
 *
 * Per-platform weighting (e.g. X counting URLs as 23) comes from the adapter's
 * capability descriptor later; this is the base count it builds on.
 */

let segmenter: Intl.Segmenter | null = null;

function getSegmenter(): Intl.Segmenter | null {
  if (segmenter) return segmenter;
  if (typeof Intl === "undefined" || typeof Intl.Segmenter !== "function") return null;
  segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  return segmenter;
}

export function countGraphemes(text: string): number {
  if (!text) return 0;
  const seg = getSegmenter();
  if (!seg) return Array.from(text).length; // code points: the closest fallback
  let n = 0;
  for (const _ of seg.segment(text)) {
    void _;
    n++;
  }
  return n;
}
