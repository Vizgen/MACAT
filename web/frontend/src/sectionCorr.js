// Geometry for the whole-section correlation scatter.
//
// Split out of the component so node can drive it: the fiddly parts are the log
// scale and the hit test, and both are wrong in ways that still LOOK like a
// scatter plot. No React, no DOM.

// Counts per cell span several decades and many genes are near zero, so the
// plot is log-log. A gene absent from one profile would be log(0), so both axes
// carry the same floor the backend correlates with -- otherwise the picture and
// the number would be computed on different data.
export const EPS = 1e-4;

export const toLog = (v) => Math.log10(Math.max(v, 0) + EPS);

/** A shared square domain for both axes.
 *
 * Square on purpose: the comparison a reader makes here is against the identity
 * line, and that line is only at 45 degrees -- only readable as "agrees" -- if
 * the two axes carry the same units per pixel. Separate per-axis domains would
 * tilt it and quietly flatter or flatten the agreement.
 */
export function domain(points, pad = 0.15) {
  if (!points || !points.length) return null;
  let lo = Infinity, hi = -Infinity;
  for (const p of points) {
    const a = toLog(p.r), b = toLog(p.q);
    if (a < lo) lo = a; if (a > hi) hi = a;
    if (b < lo) lo = b; if (b > hi) hi = b;
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  if (hi - lo < 1e-9) { lo -= 0.5; hi += 0.5; }
  const m = (hi - lo) * pad;
  return { lo: lo - m, hi: hi + m };
}

/** Decade ticks inside a log domain, thinned so labels never collide. */
export function ticks(dom, maxTicks = 6) {
  if (!dom) return [];
  const lo = Math.ceil(dom.lo), hi = Math.floor(dom.hi);
  const all = [];
  for (let e = lo; e <= hi; e++) all.push(e);
  if (all.length <= maxTicks) return all;
  const step = Math.ceil(all.length / maxTicks);
  return all.filter((_, i) => i % step === 0);
}

/** Nearest point to a screen position, or null past `maxPx`.
 *
 * Linear: there are a few hundred genes, so an index would cost more to build
 * than it saves. Distance is measured in SCREEN space, not data space -- in
 * data space the log floor packs the near-zero genes so tightly that the hit
 * test would always land on the same corner.
 */
export function nearest(points, place, sx, sy, maxPx = 14) {
  let best = null, bestD = maxPx * maxPx;
  for (let i = 0; i < points.length; i++) {
    const [px, py] = place(points[i]);
    const dx = px - sx, dy = py - sy;
    const d = dx * dx + dy * dy;
    if (d <= bestD) { bestD = d; best = points[i]; }
  }
  return best;
}

/** How far the expression-matched level sits from the one the user confirmed.
 *
 * The r-vs-level curve peaks where the EXPRESSION matches, which owes nothing to
 * the DAPI silhouette the registration used. A peak far from the confirmed level
 * is therefore independent evidence, not a restatement of the same measurement.
 *
 * `axis` is "AP" or "ML" -- the axis this section plane holds constant. The
 * direction words differ per axis and saying "posterior" about a sagittal
 * section would be simply wrong, so they come from a table rather than being
 * hardcoded. Kept in one place so the wording and the threshold cannot drift.
 */
const DIRECTION = {
  AP: { pos: "posterior", neg: "anterior" },
  ML: { pos: "left", neg: "right" },
};

export function levelVerdict(best, queryMm, axis = "AP", rAtConfirmed = null,
                             tolMm = 0.5, minGain = 0.02) {
  if (!best || queryMm == null || best.mm == null) return null;
  const off = best.mm - queryMm;
  const dir = DIRECTION[axis] || DIRECTION.AP;
  const near = Math.abs(off) <= tolMm;
  if (near) {
    return { offsetMm: off, axis, verdict: "agrees",
             text: `Expression peaks at ${axis} ${best.mm.toFixed(2)} mm, within `
                 + `${tolMm} mm of the level you confirmed.` };
  }
  // A distant peak means nothing on its own. These curves are broad -- r stays
  // within half its range over 7-9 mm -- so the argmax wanders, and a warning
  // keyed on distance alone would cry wolf on almost every job. Only call it a
  // mismatch when the peak is also MATERIALLY better than the confirmed level.
  const gain = rAtConfirmed == null ? null : best.r - rAtConfirmed;
  if (gain != null && gain < minGain) {
    return { offsetMm: off, axis, gain, verdict: "inconclusive",
             text: `The best-correlating level is ${Math.abs(off).toFixed(2)} mm `
                 + `${off > 0 ? dir.pos : dir.neg}, but only by r +${gain.toFixed(3)} `
                 + `— this curve is too flat to second-guess your ${axis} choice.` };
  }
  return {
    offsetMm: off, axis, gain, verdict: "mismatch",
    text: `Expression peaks at ${axis} ${best.mm.toFixed(2)} mm, `
        + `${Math.abs(off).toFixed(2)} mm ${off > 0 ? dir.pos : dir.neg} of the `
        + `level you confirmed`
        + (gain != null ? ` and correlates r +${gain.toFixed(3)} better there` : "")
        + ` — worth a look at step 3.`,
  };
}
