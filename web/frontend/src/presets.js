// Whole-lattice warp presets, in the spirit of Photoshop's Warp presets.
//
// Two only, chosen because they correspond to deformations a section actually
// suffers: ARC for a slice that was bowed on the slide, BULGE for one that
// swelled or shrank about its centre. The rest of the Photoshop set -- flag,
// wave, fisheye, twist -- describes nothing a brain section does, and offering
// them would invite corrections that cannot be justified afterwards.
//
// A preset is a pure function of the lattice and one amount. It returns a
// PROPOSED dst; the caller clamps each node to its kind and budget and then
// validates, exactly as the drag tools do, so a preset can no more produce an
// illegal grid than a drag can.
//
// Both are built to leave the FRAME alone: the displacement is zero on the
// outer boundary of the plane, so corners and edges stay where they are and the
// tissue outline is not dragged off the background image. That is also what
// keeps them compatible with the node-kind constraints instead of fighting
// them.
import { nodeKinds, clampNodeDisplacement, nodeDisplacementLimit,
         maxDisp } from "./warp.js";

export const PRESETS = [
  { id: "arc", label: "Arc",
    hint: "Bows the section up or down, most at the middle, nothing at the "
        + "left and right edges — for a slice that curved on the slide." },
  { id: "bulge", label: "Bulge",
    hint: "Expands or contracts about the centre, fading to nothing at the "
        + "frame — for a section that swelled or shrank overall." },
];

/** Normalised position in [-1, 1] on each axis, from a node and the extent. */
function norm(p, extent) {
  const [lr0, lr1, si0, si1] = extent;
  const cx = (lr0 + lr1) / 2, cy = (si0 + si1) / 2;
  const hw = (lr1 - lr0) / 2 || 1, hh = (si1 - si0) / 2 || 1;
  return [(p[0] - cx) / hw, (p[1] - cy) / hh];
}

/** ARC: vertical displacement following 1 - u^2 across the LR axis.
 *
 * Maximum at the middle column and zero at the left and right edges, so the
 * left/right edge nodes -- which may only move horizontally -- are asked for
 * nothing, rather than being asked for a vertical move and silently clamped.
 * The (1 - v^2) factor does the same for the top and bottom, so the frame is
 * untouched on all four sides.
 */
function arcField(p, extent, amount) {
  const [u, v] = norm(p, extent);
  return [0, amount * (1 - u * u) * (1 - v * v)];
}

/** BULGE: radial displacement about the centre, zero at the centre and at the
 *  frame, peaking in between.
 *
 * rho * (1 - rho^2) rather than a plain falloff: a bulge has to leave the
 * centre point itself alone (there is nowhere for it to go) and leave the
 * boundary alone (or it would drag the outline off the image), so the
 * displacement has to rise and fall in between.
 */
// rho * (1 - rho^2) peaks at rho = 1/sqrt(3), where it is only 0.385. Without
// this normalisation the bulge slider would reach 38% of the budget at full
// travel while the arc slider reached 100%, so the same slider position would
// mean two different strengths depending on which preset was selected.
const BULGE_PEAK = 2 / (3 * Math.sqrt(3));

function bulgeField(p, extent, amount) {
  const [u, v] = norm(p, extent);
  const rho = Math.hypot(u, v);
  if (rho < 1e-9 || rho > 1) return [0, 0];
  const mag = (amount / BULGE_PEAK) * rho * (1 - rho * rho);
  return [(u / rho) * mag, (v / rho) * mag];
}

const FIELDS = { arc: arcField, bulge: bulgeField };

/** Apply a preset to a lattice, returning a clamped dst.
 *
 * `dst0` is the lattice the gesture started from, so dragging the slider is one
 * continuous edit from a baseline rather than an accumulation -- the same shape
 * the drag tools use, and what makes the slider reversible by moving it back.
 */
export function presetGrid(gridSrc, dst0, id, amount, extent, rows, cols,
                           dispFrac = null) {
  const field = FIELDS[id];
  if (!field || !amount) return dst0.map((p) => [p[0], p[1]]);
  const kinds = nodeKinds(rows, cols);
  return dst0.map((p, i) => {
    const d = field(gridSrc[i], extent, amount);
    return clampNodeDisplacement(
      kinds[i], gridSrc[i], [p[0] + d[0], p[1] + d[1]],
      nodeDisplacementLimit(extent, kinds[i], rows, cols, dispFrac));
  });
}

/** The amount a slider position means, in microns.
 *
 * Scaled to the NODE DISPLACEMENT CAP, not to the section. Scaling to the
 * section looked natural and made most of the slider dead: on a real extent the
 * cap is ~330 um against a quarter-section of ~2200, so everything past 15% of
 * the travel clamped to the same lattice. Against the cap, full travel means
 * "the most-displaced node is at its limit" and every position does something.
 */
export function presetAmount(extent, frac, rows, cols, dispFrac = null) {
  return frac * maxDisp(extent, rows, cols, dispFrac);
}
