// Rebuilding the curated placement from a saved job.
//
// The curated annotation raster is never written to disk. What a job stores is
// the AUTOMATIC label plane (in the bundle) plus the transform the user
// confirmed, so anything that wants to draw the CURATED regions has to compose
// that transform itself -- which is what this does.
//
// It must agree with the curation canvas exactly, or Results would show
// boundaries in a place the per-cell CSV does not agree with. Curation composes
//
//     place(p) = forwardWarp(applyAffine(M, p))
//
// (CurationStep's draw path): global affine FIRST, then the local CSRBF, whose
// lattice lives in POST-AFFINE microns. Reversing the two is the plausible
// mistake -- it produces a picture that is subtly wrong rather than obviously
// broken -- so the order is pinned by tests/test_overlay_ui.mjs.
//
// No React and no DOM here, so node can drive it.
import { affineMatrix, applyAffine } from "./affine.js";
import { buildWarp, warpWeights, warpPoints } from "./warp.js";

/** A point mapper for a transform as `/api/jobs/{id}/transform` returns it.
 *
 * `transform` may be null, may carry an affine with no warp, or both. A null
 * transform is the identity, which is exactly the AUTOMATIC alignment: the
 * boundaries already arrive in the bundle's own frame.
 *
 * The CSRBF is factored once here rather than per point, so the returned
 * function is cheap enough to call inside a draw loop over every ring vertex.
 */
export function buildPlace(transform) {
  const a = transform && transform.affine;
  const M = a ? affineMatrix(a.cx, a.cy, a.tx, a.ty, a.rot_deg, a.sx, a.sy) : null;
  const aff = M ? (p) => applyAffine(M, p) : (p) => [p[0], p[1]];
  // A curation may stack several lattices. `warp_layers` is the ordered stack;
  // `warp` is the single-lattice form older jobs carry. Folding them in the
  // WRONG ORDER would still draw a warp, just not the one the labels came from.
  const stack = (transform && transform.warp_layers && transform.warp_layers.length)
    ? transform.warp_layers
    : (transform && transform.warp ? [transform.warp] : []);
  const usable = stack.filter((w) => w && w.src && w.src.length);
  if (!usable.length) return aff;
  const solved = usable.map((w) => {
    const fac = buildWarp(w.src, w.R);
    return { fac, weights: warpWeights(fac, w.dst) };
  });
  return (p) => {
    let q = aff(p);
    for (let i = 0; i < solved.length; i++) {
      q = warpPoints(solved[i].fac, solved[i].weights, [q])[0];
    }
    return q;
  };
}

/** The frame the background image covers: the bundle's displayed plane.
 *
 * Deriving this from the bounding box of the transformed regions instead would
 * slide the image against the overlay by however far curation moved the atlas,
 * which is the one misalignment this panel exists to rule out.
 */
export function planeExtent(meta) {
  if (!meta || meta.lr0 == null || meta.si0 == null) return null;
  return { x0: meta.lr0, x1: meta.lr1, y0: meta.si0, y1: meta.si1 };
}

/** Fit `extent` into a w x h canvas, in the scale/translate form the draw path
 *  uses: screen = [tx + lr * scale, ty + si * scale]. `pad` leaves a margin so
 *  a boundary on the tissue edge is not clipped by the frame. */
export function fitView(extent, w, h, pad = 0.94) {
  if (!extent || !(w > 0) || !(h > 0)) return null;
  const ew = extent.x1 - extent.x0, eh = extent.y1 - extent.y0;
  if (!(ew > 0) || !(eh > 0)) return null;
  const scale = Math.min(w / ew, h / eh) * pad;
  return { scale,
           tx: (w - ew * scale) / 2 - extent.x0 * scale,
           ty: (h - eh * scale) / 2 - extent.y0 * scale };
}

/** One entry per region present at this depth, for the visibility pills. */
export function overlayLegend(boundaries) {
  const seen = new Map();
  for (const f of ((boundaries && boundaries.features) || [])) {
    if (f.kind !== "region" || seen.has(f.acronym)) continue;
    seen.set(f.acronym, { acronym: f.acronym, name: f.name, color: f.color });
  }
  return [...seen.values()].sort((a, b) => a.acronym.localeCompare(b.acronym));
}

/** The marker background is composited server-side, so a division toggle is a
 *  new URL. `null` means every division, which is also what an absent `regions`
 *  param means to the server -- so first load needs no list. */
export function toggleDivision(all, current, acronym) {
  const cur = current || all;
  const next = cur.includes(acronym)
    ? cur.filter((a) => a !== acronym) : [...cur, acronym];
  const ordered = all.filter((a) => next.includes(a));   // keep legend order
  return ordered.length === all.length ? null : ordered;
}
