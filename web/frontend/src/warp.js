// Wendland C2 compact-support RBF local warp — mirrors pipeline_api.warp_csrbf.
// All coordinates are plane microns [lr, si]. Points farther than R from every
// anchor are left exactly where they are, so distant regions stay put.

export function wendland(t) {
  if (t >= 1) return 0;
  const u = 1 - t;
  return u * u * u * u * (4 * t + 1);
}

function dist(a, b) {
  const dx = a[0] - b[0], dy = a[1] - b[1];
  return Math.hypot(dx, dy);
}

// LU decomposition with partial pivoting of an n x n matrix (in place copy).
function luDecompose(A) {
  const n = A.length;
  const lu = A.map((r) => r.slice());
  const piv = Array.from({ length: n }, (_, i) => i);
  for (let k = 0; k < n; k++) {
    let p = k, max = Math.abs(lu[k][k]);
    for (let i = k + 1; i < n; i++) {
      const v = Math.abs(lu[i][k]);
      if (v > max) { max = v; p = i; }
    }
    if (p !== k) { [lu[k], lu[p]] = [lu[p], lu[k]]; [piv[k], piv[p]] = [piv[p], piv[k]]; }
    const akk = lu[k][k] || 1e-12;
    for (let i = k + 1; i < n; i++) {
      lu[i][k] /= akk;
      const f = lu[i][k];
      for (let j = k + 1; j < n; j++) lu[i][j] -= f * lu[k][j];
    }
  }
  return { lu, piv, n };
}

// Solve L U x = P b for a single RHS vector.
function luSolve({ lu, piv, n }, b) {
  const y = new Array(n);
  for (let i = 0; i < n; i++) {
    let s = b[piv[i]];
    for (let j = 0; j < i; j++) s -= lu[i][j] * y[j];
    y[i] = s;
  }
  const x = new Array(n);
  for (let i = n - 1; i >= 0; i--) {
    let s = y[i];
    for (let j = i + 1; j < n; j++) s -= lu[i][j] * x[j];
    x[i] = s / (lu[i][i] || 1e-12);
  }
  return x;
}

// Build a reusable warp bound to fixed anchor origins `src` and radius R.
// The kernel matrix depends only on src+R, so we factor it once; dragging only
// changes the right-hand side (dst - src) and is a cheap re-solve.
export function buildWarp(src, R) {
  const n = src.length;
  if (n === 0) return { src, R, n: 0 };
  const K = [];
  for (let i = 0; i < n; i++) {
    const row = new Array(n);
    for (let j = 0; j < n; j++) row[j] = wendland(dist(src[i], src[j]) / R);
    row[i] += 1e-6; // tiny ridge for stability
    K.push(row);
  }
  return { src, R, n, fac: luDecompose(K) };
}

// Solve for the RBF weights given the current dragged targets `dst`.
export function warpWeights(warp, dst) {
  const { src, n, fac } = warp;
  if (n === 0) return [];
  const vx = new Array(n), vy = new Array(n);
  for (let i = 0; i < n; i++) { vx[i] = dst[i][0] - src[i][0]; vy[i] = dst[i][1] - src[i][1]; }
  const wx = luSolve(fac, vx), wy = luSolve(fac, vy);
  return wx.map((_, i) => [wx[i], wy[i]]);
}

// Forward-warp arbitrary points [ [lr,si], ... ] given precomputed weights.
export function warpPoints(warp, weights, pts) {
  const { src, R, n } = warp;
  if (n === 0) return pts;
  const out = new Array(pts.length);
  for (let k = 0; k < pts.length; k++) {
    const p = pts[k];
    let dx = 0, dy = 0;
    for (let i = 0; i < n; i++) {
      const ph = wendland(dist(p, src[i]) / R);
      if (ph === 0) continue;
      dx += ph * weights[i][0];
      dy += ph * weights[i][1];
    }
    out[k] = [p[0] + dx, p[1] + dy];
  }
  return out;
}

// ===========================================================================
// Sparse warp GRID (Phase 2) — mirrors pipeline_api.py exactly. src = regular
// node lattice, dst = user-dragged; the FORWARD field (centers = src) warps the
// atlas overlay for the preview, and the backend finalize inverts it (CSRBF
// pull) so the preview boundaries and the final label raster coincide. Grid
// BORDER nodes are held fixed; only interior nodes move. All constants below MUST
// stay numerically identical to their WARP_* counterparts in pipeline_api.py.
// ===========================================================================
export const GRID_ALGO = "wendland_csrbf_grid_v1";
// DEFAULT grid for a new curation: 13x13 = 121 free interior nodes plus 44
// constrained edge nodes. Every function accepts rows/cols, and a persisted warp
// carries its own, so an older job still restores on its saved lattice.
export const GRID_ROWS = 13;
export const GRID_COLS = 13;
// Compatibility aliases retained for older callers that named the fine preset.
export const FINE_GRID_ROWS = 13;
export const FINE_GRID_COLS = 13;
export const MAX_GRID_DIM = 15;

// Lattice densities offered in Local correction, fine -> coarse.
//
// The per-node cap is a FRACTION of node spacing, so coarsening the lattice
// scales how far a correction reaches while leaving the deformation's strain --
// and therefore the fold gate -- essentially untouched. Measured on a
// 16750 x 8250 um section, one interior node pushed to its limit:
//
//     lattice   interior nodes   cap um   reach um   x vs 13x13   min |J|
//      13x13         121           309       309        1.0        0.407
//       9x9           49           464       464        1.5        0.407
//       7x7           25           619       619        2.0        0.407
//       5x5            9           928       928        3.0        0.408
//       4x4            4          1238      1237        4.0        0.418
//
// min |J| barely moves: 4x4 is not four times more distorted, it is the same
// distortion spread over four times the distance.
//
// ELASTIC PUSH DOES NOT FOLLOW THE SAME CURVE, which is why this is a picker and
// not a new default. A 4 x 2.4 mm region pushed to its limit reaches 1.0x /
// 1.7x / 1.9x / 1.3x of the 13x13 baseline at those four densities -- it peaks
// at 5x5 and is WORSE at 4x4, because a push is a field and four interior nodes
// cannot express its shape, so it folds sooner. Measured, a 4 x 2.4 mm region
// pushed to its limit:
//
//     lattice     si um   lr um    si x    lr x
//      13x13       1236     464     1.0     1.0
//       9x9        1703     666     1.4     1.4
//       7x7        2043     928     1.7     2.0
//       5x5        2348    1332     1.9     2.9   <- best for the push
//       4x4        1568     911     1.3     2.0   <- worse than 5x5
//
// The notes quoted BOTH numbers while elastic push was offered, because the two
// tools disagree about which density is best. With elastic push hashed out
// (2026-11-03) they quote node drag only, relative to the 13x13 default: the
// cap scales with node spacing, i.e. with 12 / (n - 1).
// No `label`: the buttons read "13x13" and the note carries the meaning, so a
// word for each density was one more thing to keep true and nothing rendered it.
//
// 4x4 and 7x7 were dropped on 2026-11-03 (Developer CYC). 17x17 was tried the same
// day and dropped as too slow, so MAX_GRID_DIM stays 15. Saved warps at any
// size up to MAX_GRID_DIM still restore on their own lattice.
export const GRID_PRESETS = [
  { rows: 13, cols: 13, note: "most local control; the default" },
  { rows: 9, cols: 9, note: "1.5x node drag" },
  { rows: 5, cols: 5, note: "3x node drag, the widest reach" },
];
export const GRID_RADIUS_FACTOR = 1.5;   // R = factor * mean node spacing
export const MAX_DISP_FRAC = 0.45;       // max |dst-src| / min node spacing
// A warp may carry its own dispFrac to buy range for drastic edits (the elastic
// push tool); it is clamped to this ceiling on BOTH sides. The cap is a
// convenience, not a safety property -- node ordering and the Jacobian gate are
// what protect the label raster, and measurement shows the fold gate is what
// fires first at every lattice size, so the ceiling is set high enough to hand
// the decision to it. See the rationale table on WARP_MAX_DISP_FRAC_CEILING in
// pipeline_api.py, which this value MUST equal.
export const MAX_DISP_FRAC_CEILING = 3.0;

// LAYERED WARPS. A curation may stack lattices, applied in order, instead of
// forcing one lattice to carry the whole correction: bake the current grid and
// keep dragging on a fresh identity grid. Mirrors WARP_MAX_LAYERS and
// WARP_COMPOSED_JAC_MIN; see the measurements on those.
//
// This is not a way around the fold gate. Every layer is validated on its own
// AND the stack is gated as one map, because determinants multiply -- eight
// individually legal layers compose to min |J| 0.010-0.099 on a real section,
// which the composed floor refuses.
export const MAX_LAYERS = 8;
export const COMPOSED_JAC_MIN = 0.10;

/** Fold a stack of baked layers, in order, into one point mapper.
 *
 * Each layer is {src, dst, R, fac}. `fac` is the LU-factored kernel, kept so a
 * redraw does not re-solve it; weights depend only on dst, so they are solved
 * once here rather than per point.
 */
export function chainPlacer(layers) {
  if (!layers || !layers.length) return (p) => p;
  const solved = layers.map((ly) => ({
    fac: ly.fac || buildWarp(ly.src, ly.R),
    dst: ly.dst,
  }));
  const weights = solved.map((s) => warpWeights(s.fac, s.dst));
  return (p) => {
    let q = p;
    for (let i = 0; i < solved.length; i++) {
      q = warpPoints(solved[i].fac, weights[i], [q])[0];
    }
    return q;
  };
}

/** min determinant of the COMPOSED map over the plane, mirroring
 *  warp_composed_jac. The browser checks this too, so Confirm reflects the
 *  verdict the backend will reach rather than discovering it after a round trip. */
// samples MUST match warp_composed_jac's default: a coarser grid here reads a
// slightly higher minimum, and the browser would show green on a stack the
// backend then refuses -- the round trip this mirror exists to avoid.
export function composedJacMin(layers, extent, samples = 41, eps = 1.0) {
  if (!layers || !layers.length) return 1;
  const place = chainPlacer(layers);
  const [lr0, lr1, si0, si1] = extent;
  let mn = Infinity;
  for (let i = 0; i < samples; i++) {
    const x = lr0 + eps + (lr1 - lr0 - 2 * eps) * (i / (samples - 1));
    for (let j = 0; j < samples; j++) {
      const y = si0 + eps + (si1 - si0 - 2 * eps) * (j / (samples - 1));
      const px = place([x + eps, y]), mx = place([x - eps, y]);
      const py = place([x, y + eps]), my = place([x, y - eps]);
      const a = (px[0] - mx[0]) / (2 * eps), b = (py[0] - my[0]) / (2 * eps);
      const c = (px[1] - mx[1]) / (2 * eps), d = (py[1] - my[1]) / (2 * eps);
      const det = a * d - b * c;
      if (Number.isFinite(det) && det < mn) mn = det;
    }
  }
  return mn === Infinity ? 1 : mn;
}
export const JAC_MIN = 0.05;             // reject fold / extreme compression
// Stricter fold floor once a warp opts into a raised dispFrac. JAC_MIN was
// calibrated when nothing could move more than ~257 um; at the new range a grid
// can sit on det = 0.05, pass here, and then fail to invert in finalize -- after
// the user has confirmed. Measured rationale on WARP_JAC_MIN_BOOSTED in
// pipeline_api.py, which this value MUST equal.
export const JAC_MIN_BOOSTED = 0.50;
export const JAC_MAX = 20.0;             // reject extreme stretch
export const JAC_SAMPLES = 21;           // FLOOR for the NxN Jacobian sampling grid
export const JAC_PER_CELL = 5;           // samples per lattice cell, per axis
export const JAC_EPS = 1.0;              // micron step for the central difference

// Keep samples-per-lattice-cell constant as the grid densifies; mirrors
// pipeline_api.warp_jac_samples (21 at 5x5, 31 at 7x7).
export function jacSamples(rows = GRID_ROWS, cols = GRID_COLS) {
  return Math.max(JAC_SAMPLES, JAC_PER_CELL * (Math.max(rows, cols) - 1) + 1);
}

// Regular lattice over the displayed extent [lr0, lr1, si0, si1]; row-major
// (si outer, lr inner) — identical ordering to pipeline_api.warp_grid_nodes.
export function gridNodes(extent, rows = GRID_ROWS, cols = GRID_COLS) {
  const [lr0, lr1, si0, si1] = extent;
  const nodes = [];
  for (let r = 0; r < rows; r++) {
    const si = rows === 1 ? si0 : si0 + ((si1 - si0) * r) / (rows - 1);
    for (let c = 0; c < cols; c++) {
      const lr = cols === 1 ? lr0 : lr0 + ((lr1 - lr0) * c) / (cols - 1);
      nodes.push([lr, si]);
    }
  }
  return nodes;
}

export function gridRadius(extent, rows = GRID_ROWS, cols = GRID_COLS) {
  const [lr0, lr1, si0, si1] = extent;
  const spLr = (lr1 - lr0) / (cols - 1);
  const spSi = (si1 - si0) / (rows - 1);
  return GRID_RADIUS_FACTOR * 0.5 * (spLr + spSi);
}

// True where a node is on the grid BORDER. Border != immovable: see nodeKinds.
export function edgeMask(rows = GRID_ROWS, cols = GRID_COLS) {
  const m = new Array(rows * cols).fill(false);
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++)
      if (r === 0 || r === rows - 1 || c === 0 || c === cols - 1)
        m[r * cols + c] = true;
  return m;
}

// Degrees of freedom per node -- mirrors pipeline_api.warp_node_kinds.
export const NODE_CORNER = 0;   // fixed
export const NODE_EDGE_V = 1;   // top/bottom edge: si only
export const NODE_EDGE_H = 2;   // left/right edge: lr only
export const NODE_FREE = 3;     // interior: 2D
export const EDGE_DISP_FRAC = 0.6;   // edge cap as a fraction of the interior cap

export function nodeKinds(rows = GRID_ROWS, cols = GRID_COLS) {
  const k = new Array(rows * cols).fill(NODE_FREE);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const top = r === 0 || r === rows - 1;
      const side = c === 0 || c === cols - 1;
      if (top && side) k[r * cols + c] = NODE_CORNER;
      else if (top) k[r * cols + c] = NODE_EDGE_V;
      else if (side) k[r * cols + c] = NODE_EDGE_H;
    }
  }
  return k;
}

// Project a proposed node position onto the axis that node is allowed to move on,
// so a drag can never produce a position the validator would reject outright.
export function constrainNode(kind, src, proposed) {
  if (kind === NODE_CORNER) return [src[0], src[1]];
  if (kind === NODE_EDGE_V) return [src[0], proposed[1]];   // vertical only
  if (kind === NODE_EDGE_H) return [proposed[0], src[1]];   // horizontal only
  return [proposed[0], proposed[1]];
}

// Interior per-node displacement cap in microns; mirrors pipeline_api.warp_max_disp.
// `dispFrac` null/undefined keeps the historical MAX_DISP_FRAC, so every grid the
// affine, direct-drag and node tools author is unaffected.
export function maxDisp(
  extent, rows = GRID_ROWS, cols = GRID_COLS, dispFrac = null,
) {
  const [lr0, lr1, si0, si1] = extent;
  const spLr = (lr1 - lr0) / (cols - 1);
  const spSi = (si1 - si0) / (rows - 1);
  let frac = dispFrac == null ? MAX_DISP_FRAC : Number(dispFrac);
  if (!Number.isFinite(frac) || frac <= 0) frac = MAX_DISP_FRAC;
  frac = Math.min(frac, MAX_DISP_FRAC_CEILING);
  return frac * Math.min(spLr, spSi);
}

// Maximum legal displacement for one node, in plane microns. Keeping this
// calculation here makes the drag interaction use the exact same cap as
// validateGrid instead of letting the pointer overshoot and rejecting on release.
export function nodeDisplacementLimit(
  extent, kind, rows = GRID_ROWS, cols = GRID_COLS, dispFrac = null,
) {
  const interior = maxDisp(extent, rows, cols, dispFrac);
  return kind === NODE_FREE ? interior : EDGE_DISP_FRAC * interior;
}

// Project onto the node's legal axis and clamp to its displacement budget.
// A long or imprecise pointer movement therefore stops at the legal boundary
// instead of turning the grid red and rolling the whole drag back.
export function clampNodeDisplacement(kind, src, proposed, limit) {
  const p = constrainNode(kind, src, proposed);
  const dx = p[0] - src[0], dy = p[1] - src[1];
  const d = Math.hypot(dx, dy);
  if (!Number.isFinite(limit) || d <= limit || d === 0) return p;
  const k = limit / d;
  return [src[0] + dx * k, src[1] + dy * k];
}

// How many nodes the user can move at all (everything but the 4 corners).
export function movableCount(rows = GRID_ROWS, cols = GRID_COLS) {
  return rows * cols - 4;
}

// One-shot forward warp of `pts` (mirrors pipeline_api.warp_csrbf_points):
// builds the kernel on src, solves for dst-src, applies p + phi(p).
export function forwardWarp(src, dst, R, pts) {
  if (!src.length || R <= 0) return pts.map((p) => [p[0], p[1]]);
  let moved = false;
  for (let i = 0; i < src.length; i++)
    if (src[i][0] !== dst[i][0] || src[i][1] !== dst[i][1]) { moved = true; break; }
  if (!moved) return pts.map((p) => [p[0], p[1]]);
  const warp = buildWarp(src, R);
  const w = warpWeights(warp, dst);
  return warpPoints(warp, w, pts);
}

// Recover pre-warp coordinates for a point clicked on the currently warped
// overlay. Fixed-point correction is sufficient for the small, validated
// deformations allowed by this editor and mirrors the backend's iterative pull.
export function inverseWarpPoint(src, dst, R, target, iterations = 16) {
  let p = [target[0], target[1]];
  for (let i = 0; i < iterations; i++) {
    const q = forwardWarp(src, dst, R, [p])[0];
    p = [p[0] + target[0] - q[0], p[1] + target[1] - q[1]];
  }
  return p;
}

// Convert a small set of explicit atlas->tissue correspondences into the existing
// regular-grid representation used by preview, validation and backend finalize.
// This changes only how grid targets are authored; the persisted warp format and
// CSRBF math remain unchanged.
export function landmarksToGrid(
  gridSrc, pairs, R, extent, rows = GRID_ROWS, cols = GRID_COLS,
) {
  if (!pairs.length) return gridSrc.map((p) => [p[0], p[1]]);
  const src = pairs.map((p) => p.atlas);
  const dst = pairs.map((p) => p.tissue);
  const proposed = forwardWarp(src, dst, R, gridSrc);
  const kinds = nodeKinds(rows, cols);
  return proposed.map((p, i) => clampNodeDisplacement(
    kinds[i],
    gridSrc[i],
    p,
    nodeDisplacementLimit(extent, kinds[i], rows, cols),
  ));
}

// Apply one direct line pull to the regular grid with a Gaussian falloff.
// Corners, edge axes and displacement budgets remain governed by the exact
// same constraints used by manual node dragging and backend validation.
export function directDragGrid(
  gridSrc, dst0, anchor, delta, radius, extent,
  rows = GRID_ROWS, cols = GRID_COLS,
) {
  const kinds = nodeKinds(rows, cols);
  return dst0.map((p, i) => {
    const d = Math.hypot(
      gridSrc[i][0] - anchor[0],
      gridSrc[i][1] - anchor[1],
    );
    const weight = d > radius * 2.5
      ? 0 : Math.exp(-0.5 * (d / radius) ** 2);
    return clampNodeDisplacement(
      kinds[i],
      gridSrc[i],
      [p[0] + delta[0] * weight, p[1] + delta[1] * weight],
      nodeDisplacementLimit(extent, kinds[i], rows, cols),
    );
  });
}

// det of the FORWARD Jacobian at each point (central difference); >0 => no fold.
export function jacobianDets(src, dst, R, pts, eps = JAC_EPS) {
  const px = pts.map((p) => [p[0] + eps, p[1]]);
  const mx = pts.map((p) => [p[0] - eps, p[1]]);
  const py = pts.map((p) => [p[0], p[1] + eps]);
  const my = pts.map((p) => [p[0], p[1] - eps]);
  const fpx = forwardWarp(src, dst, R, px), fmx = forwardWarp(src, dst, R, mx);
  const fpy = forwardWarp(src, dst, R, py), fmy = forwardWarp(src, dst, R, my);
  const out = new Array(pts.length);
  for (let k = 0; k < pts.length; k++) {
    const axx = (fpx[k][0] - fmx[k][0]) / (2 * eps);
    const ayx = (fpx[k][1] - fmx[k][1]) / (2 * eps);
    const axy = (fpy[k][0] - fmy[k][0]) / (2 * eps);
    const ayy = (fpy[k][1] - fmy[k][1]) / (2 * eps);
    out[k] = axx * ayy - ayx * axy;
  }
  return out;
}

// Fold floor for a warp; mirrors pipeline_api.warp_jac_min.
export function jacMin(dispFrac = null) {
  return dispFrac == null ? JAC_MIN : JAC_MIN_BOOSTED;
}

// Guard a candidate grid — mirrors pipeline_api.validate_warp_grid. Returns
// { ok, reason, minDet, maxDet, maxDisp, nMoved }.
export function validateGrid(src, dst, R, extent, rows = GRID_ROWS, cols = GRID_COLS,
                             dispFrac = null) {
  if (!Number.isInteger(rows) || !Number.isInteger(cols)
      || rows < 3 || cols < 3 || rows > MAX_GRID_DIM || cols > MAX_GRID_DIM) {
    return { ok: false, reason: "unsupported grid dimensions", minDet: null,
             maxDet: null, maxDisp: null, nMoved: 0 };
  }
  const [lr0, lr1, si0, si1] = extent;
  // NOT named maxDisp: that is the exported cap helper, and the result dict
  // already uses `maxDisp` for the largest displacement actually observed.
  const dispCap = maxDisp(extent, rows, cols, dispFrac);
  const kinds = nodeKinds(rows, cols);
  let dmax = 0, nMoved = 0, anyMoved = false;
  const fail = (reason) => ({ ok: false, reason, minDet: null, maxDet: null,
                              maxDisp: dmax, nMoved });
  // (1) per-node degrees of freedom, same order and wording as the backend
  for (let i = 0; i < src.length; i++) {
    const dx = dst[i][0] - src[i][0], dy = dst[i][1] - src[i][1];
    const d = Math.hypot(dx, dy);
    if (d > dmax) dmax = d;
    if (d > 1e-6) { nMoved++; anyMoved = true; }
    if (d > 1e-6 && kinds[i] === NODE_CORNER) return fail("corner control point moved");
    if (Math.abs(dx) > 1e-6 && kinds[i] === NODE_EDGE_V)
      return fail("top/bottom edge node moved horizontally");
    if (Math.abs(dy) > 1e-6 && kinds[i] === NODE_EDGE_H)
      return fail("left/right edge node moved vertically");
  }
  // (2) displacement cap: tighter on the edge than inside
  for (let i = 0; i < src.length; i++) {
    const d = Math.hypot(dst[i][0] - src[i][0], dst[i][1] - src[i][1]);
    const lim = kinds[i] === NODE_FREE ? dispCap : EDGE_DISP_FRAC * dispCap;
    if (d > lim + 1e-6) return fail("control-point displacement exceeds limit");
  }
  // (3) no node may cross a neighbour: lr increasing along rows, si along columns
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols - 1; c++)
      if (dst[r * cols + c + 1][0] - dst[r * cols + c][0] <= 0)
        return fail("grid nodes crossed a neighbour");
  for (let c = 0; c < cols; c++)
    for (let r = 0; r < rows - 1; r++)
      if (dst[(r + 1) * cols + c][1] - dst[r * cols + c][1] <= 0)
        return fail("grid nodes crossed a neighbour");
  if (!anyMoved)
    return { ok: true, reason: "identity", minDet: 1, maxDet: 1, maxDisp: 0, nMoved: 0 };
  const N = jacSamples(rows, cols), pts = [];
  for (let r = 0; r < N; r++) {
    const y = si0 + ((si1 - si0) * r) / (N - 1);
    for (let c = 0; c < N; c++) pts.push([lr0 + ((lr1 - lr0) * c) / (N - 1), y]);
  }
  const dets = jacobianDets(src, dst, R, pts);
  let mn = Infinity, mx = -Infinity;
  for (const d of dets) { if (d < mn) mn = d; if (d > mx) mx = d; }
  if (mn <= jacMin(dispFrac) || mx >= JAC_MAX)
    return { ok: false, reason: `warp folds or over-stretches (det in [${mn.toFixed(3)}, ${mx.toFixed(3)}])`,
      minDet: mn, maxDet: mx, maxDisp: dmax, nMoved };
  return { ok: true, reason: "ok", minDet: mn, maxDet: mx, maxDisp: dmax, nMoved };
}

