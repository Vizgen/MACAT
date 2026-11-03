// ===========================================================================
// Elastic push — regularized Kelvinlets (de Goes & James 2017), "grab" brush.
//
// The CSRBF grid warp in warp.js is an INTERPOLATOR: you state where control
// points go and it fills in between. That makes it excellent for local tweaks
// and poor at "shove this nucleus across the section and let the neighbours
// take up the slack", because every node must be placed by hand.
//
// This module is a FIELD: one drag produces an analytic displacement everywhere,
// from the closed-form solution of the Navier-Cauchy equations of linear
// elasticity for a point load on an infinite solid, regularized so it stays
// finite at the load. Tissue behaves like an elastic body — the grabbed region
// translates, the material ahead of it compresses and the material to the side
// is pushed around it.
//
// IMPORTANT — this module never becomes a persisted format. It authors node
// displacements into the SAME Wendland lattice every other tool writes
// ({algo, rows, cols, R, src, dst}), exactly as warp.js `landmarksToGrid` and
// `directDragGrid` do. The backend therefore needs no Kelvinlet code, the
// cross-language parity harnesses are unaffected, and a warp authored here is
// validated by the same validate_warp_grid as any other.
//
// A useful consequence: validateGrid sees the CSRBF INTERPOLATION of this field,
// not the field itself, so the one non-smooth locus below (the exterior medial
// axis of the grabbed polygon) is smoothed away before it can trip the Jacobian
// gate.
// ===========================================================================
import {
  nodeKinds, clampNodeDisplacement, nodeDisplacementLimit,
  warpWeights, warpPoints, forwardWarp,
} from "./warp.js";

// Poisson ratio. 0.5 is incompressible — the material has to go somewhere, so
// neighbours are squeezed AROUND the moving region (the oil-droplet look).
// Toward 0 the material compresses instead and neighbours are simply dragged
// along. Kept strictly below 0.5: b diverges at exactly 0.5 for the full
// Kelvinlet family, and the grab brush loses its lateral term.
export const NU_MIN = 0.0;
export const NU_MAX = 0.49;
export const DEFAULT_NU = 0.45;

// Push radius as a fraction of the smaller extent axis. This is the dial that
// replaces "local only" — it sets how far the deformation reaches, independent
// of the lattice.
export const DEFAULT_EPS_FRAC = 0.25;

// The anchor-matching rescale (see elasticPushGrid) may amplify, but never
// without bound: a pathological geometry must not be able to blow the lattice
// out in one frame.
export const MAX_ANCHOR_GAIN = 3.0;

// --------------------------------------------------------------- geometry
// Rings arrive from /api/boundaries as [{role: "exterior"|"hole", points}].
// A bare array of points is accepted too so callers and tests can stay terse.
export function asRings(poly) {
  if (!poly || !poly.length) return [];
  if (Array.isArray(poly[0]) && typeof poly[0][0] === "number") {
    return [poly];                                   // a single flat ring
  }
  return poly.map((r) => (Array.isArray(r) ? r : r.points)).filter(Boolean);
}

// Even-odd containment across every ring, so a hole in the region counts as
// outside. Standard ray cast, same convention as pointInRing in CurationStep.
export function pointInRings(poly, p) {
  let inside = false;
  for (const ring of asRings(poly)) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if ((yi > p[1]) !== (yj > p[1])
          && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi + 1e-300) + xi) {
        inside = !inside;
      }
    }
  }
  return inside;
}

// Squared distance from p to segment ab, plus the closest point on it.
function segClosest(p, a, b) {
  const vx = b[0] - a[0], vy = b[1] - a[1];
  const wx = p[0] - a[0], wy = p[1] - a[1];
  const len2 = vx * vx + vy * vy;
  const t = len2 <= 0 ? 0 : Math.max(0, Math.min(1, (wx * vx + wy * vy) / len2));
  const cx = a[0] + t * vx, cy = a[1] + t * vy;
  const dx = p[0] - cx, dy = p[1] - cy;
  return [dx * dx + dy * dy, cx, cy];
}

// Distance from p to the polygon, and the outward unit direction from the
// nearest point on the outline to p.
//
// `d` is 0 anywhere INSIDE. That is the whole trick behind the rigid core: the
// Kelvinlet is then evaluated at radius d rather than at distance from a point,
// so it returns exactly the drag delta across the entire interior — the region
// translates rigidly by construction — and falls off from the OUTLINE, which is
// what makes neighbours wrap around the shape instead of around a point.
export function polygonDistance(poly, p) {
  const rings = asRings(poly);
  if (!rings.length) return { d: 0, dir: [0, 0] };
  if (pointInRings(rings, p)) return { d: 0, dir: [0, 0] };
  let best = Infinity, bx = p[0], by = p[1];
  for (const ring of rings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [d2, cx, cy] = segClosest(p, ring[j], ring[i]);
      if (d2 < best) { best = d2; bx = cx; by = cy; }
    }
  }
  const d = Math.sqrt(best);
  if (d <= 1e-9) return { d: 0, dir: [0, 0] };
  return { d, dir: [(p[0] - bx) / d, (p[1] - by) / d] };
}

// ---------------------------------------------------------------- kernel
// Regularized Kelvinlet grab, normalised so u(0) === delta exactly.
//
//   u(r) = [ (a - b)/r_e + a e^2 / (2 r_e^3) ] f  +  [ b / r_e^3 ] (r . f) r
//   r_e  = sqrt(|r|^2 + e^2)        a = 1/(4 pi mu)     b = a / (4 (1 - nu))
//
// mu only scales the load, and the load is chosen below to hit `delta` at the
// origin, so it cancels: fix a = 1. At r = 0 the bracket collapses to
// (3a/2 - b)/e, hence f = delta * e / (3/2 - b). That factor is in [1.0, 1.25]
// over nu in [0, 0.5], so it can never vanish.
export function kelvinletGrab(r, delta, eps, nu = DEFAULT_NU) {
  const e = Math.max(1e-9, eps);
  const nuc = Math.min(NU_MAX, Math.max(NU_MIN, nu));
  const a = 1.0;
  const b = a / (4 * (1 - nuc));
  const scale = e / (1.5 * a - b);                   // load that gives u(0)=delta
  const fx = delta[0] * scale, fy = delta[1] * scale;

  const r2 = r[0] * r[0] + r[1] * r[1];
  const re = Math.sqrt(r2 + e * e);
  const re3 = re * re * re;
  const iso = (a - b) / re + (a * e * e) / (2 * re3);   // pushes along the load
  const dot = r[0] * fx + r[1] * fy;
  const lat = (b / re3) * dot;                         // pushes along r: the squeeze
  return [iso * fx + lat * r[0], iso * fy + lat * r[1]];
}

// The field a whole drag produces at an arbitrary point, measured from the
// grabbed polygon's outline. Exported for tests and for drawing a preview.
export function elasticField(poly, p, delta, eps, nu = DEFAULT_NU) {
  const { d, dir } = polygonDistance(poly, p);
  if (d <= 0) return [delta[0], delta[1]];           // rigid core
  return kelvinletGrab([dir[0] * d, dir[1] * d], delta, eps, nu);
}

// ------------------------------------------------------------- authoring
// Apply one elastic push to the lattice. Deliberately the same shape as
// warp.js `directDragGrid` so it drops into the existing drag machinery:
// displacements accumulate onto `dst0` (the lattice as it was at pointer-down),
// and every node is clamped by the same helpers the node drag and the backend
// validator use.
//
// `anchor` is the grab point in LATTICE space (un-warp the click with
// inverseWarpPoint first, exactly as the direct tool does). `fac` is the
// prebuilt LU factorization from buildWarp — pass it to avoid re-factoring a
// 169x169 kernel on every pointer move.
export function elasticPushGrid(
  gridSrc, dst0, poly, anchor, delta, eps, nu, extent,
  rows, cols, dispFrac = null, R = null, fac = null,
) {
  const kinds = nodeKinds(rows, cols);
  const limit = (i) => nodeDisplacementLimit(extent, kinds[i], rows, cols, dispFrac);

  const raw = gridSrc.map((s) => elasticField(poly, s, delta, eps, nu));
  const build = (gain) => dst0.map((p, i) => clampNodeDisplacement(
    kinds[i], gridSrc[i],
    [p[0] + raw[i][0] * gain, p[1] + raw[i][1] * gain],
    limit(i),
  ));

  let dst = build(1);
  // Anchor matching. A region smaller than one lattice cell contains no node,
  // so nothing is pinned to `delta` and the grabbed anatomy lags the cursor --
  // measured as the common case, 24 of 34 depth-2 regions at 13x13. The field is
  // linear in the node displacements, so one probe gives the exact gain; clamping
  // afterwards is what keeps it legal.
  const evalAt = (nodes, pts) => (
    fac ? warpPoints(fac, warpWeights(fac, nodes), pts)
        : forwardWarp(gridSrc, nodes, R, pts)
  );
  if ((R || fac) && anchor) {
    const before = evalAt(dst0, [anchor])[0];
    const after = evalAt(dst, [anchor])[0];
    const got = Math.hypot(after[0] - before[0], after[1] - before[1]);
    const want = Math.hypot(delta[0], delta[1]);
    if (got > 1e-9 && want > 1e-9) {
      const gain = Math.min(MAX_ANCHOR_GAIN, Math.max(1, want / got));
      if (gain > 1.0001) dst = build(gain);
    }
  }
  return dst;
}

// Suggested default push radius for an extent, in plane microns.
export function defaultEps(extent) {
  const [lr0, lr1, si0, si1] = extent;
  return DEFAULT_EPS_FRAC * Math.min(lr1 - lr0, si1 - si0);
}
