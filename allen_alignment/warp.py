"""Wendland CSRBF deformation: the kernel, the control lattice, the forward and
inverse fields, and validate_warp_grid.

This module is MIRRORED by web/frontend/src/warp.js and the two are held
numerically identical by validation/_grid_parity.py. Any constant or rule
changed here must be changed there in the same commit."""
import numpy as np
from .plane import (_plane_spacing)


# ---------------------------------------------------------------------------
# Wendland compact-support RBF (CSRBF) — LOCAL smooth warp for boundary editing.
# Dragging an anchor only affects points within the influence radius R (microns);
# beyond R the displacement is exactly zero, so distant regions stay put.
# Everything works in plane microns [lr, si] so the browser preview (also microns)
# and this authoritative label warp use identical math.
# ---------------------------------------------------------------------------
def _wendland(t):
    """Wendland C2 phi(t) = (1-t)^4 (4t+1) on [0,1], 0 for t>=1. Compact support."""
    t = np.asarray(t, float)
    out = np.zeros_like(t)
    m = t < 1.0
    tm = t[m]
    out[m] = (1.0 - tm) ** 4 * (4.0 * tm + 1.0)
    return out



def _csrbf_weights(centers, values, R):
    """Solve K w = values for a compact-support RBF interpolant. centers [n,2] um,
    values [n,2] um displacement, R scalar radius (um). Returns weights [n,2]."""
    centers = np.asarray(centers, float)
    values = np.asarray(values, float)
    d = np.linalg.norm(centers[:, None, :] - centers[None, :, :], axis=-1)
    K = _wendland(d / R)
    K = K + 1e-6 * np.eye(len(centers))  # tiny ridge for stability
    return np.linalg.solve(K, values)



def _csrbf_eval(centers, weights, R, pts):
    """Evaluate the CSRBF displacement field at pts [m,2] -> [m,2]."""
    centers = np.asarray(centers, float)
    pts = np.asarray(pts, float)
    d = np.linalg.norm(pts[:, None, :] - centers[None, :, :], axis=-1)
    W = _wendland(d / R)            # [m, n]
    return W @ weights             # [m, 2]



def warp_csrbf_points(src_pts_um, dst_pts_um, R, pts_um):
    """FORWARD local warp (src->dst) applied to arbitrary plane-micron points.
    Mirrors the browser preview exactly. Points > R from every anchor are unmoved."""
    src = np.asarray(src_pts_um, float).reshape(-1, 2)
    dst = np.asarray(dst_pts_um, float).reshape(-1, 2)
    pts = np.asarray(pts_um, float).reshape(-1, 2)
    if len(src) < 1 or R <= 0 or np.allclose(src, dst):
        return pts
    w = _csrbf_weights(src, dst - src, R)
    return pts + _csrbf_eval(src, w, R, pts)



def _csrbf_field_and_jac(centers, weights, R, pts):
    """CSRBF displacement field AND its analytic Jacobian at pts.

    phi(t) = (1-t)^4 (4t+1)  =>  dphi/dt = -20 t (1-t)^3, so

        d/dp_b  phi(|p-c|/R) = (dphi/dt)/R * (p_b - c_b)/r
                             = -20 (1-t)^3 / R^2 * (p_b - c_b)

    -- the 1/r cancels, so the derivative is finite at p == c. Returns
    (disp [m,2], jac [m,2,2]) where jac[m,a,b] = d disp_a / d p_b.
    """
    centers = np.asarray(centers, float)
    pts = np.asarray(pts, float)
    diff = pts[:, None, :] - centers[None, :, :]          # [m, n, 2]
    r = np.linalg.norm(diff, axis=-1)                     # [m, n]
    t = r / R
    W = _wendland(t)                                      # [m, n]
    g = np.where(t < 1.0, -20.0 * (1.0 - t) ** 3 / (R * R), 0.0)
    disp = W @ weights                                    # [m, 2]
    jac = np.einsum("mi,ia,mib->mab", g, weights, diff)   # [m, 2, 2]
    return disp, jac



# Numerical inverse of the FORWARD CSRBF field. The browser preview draws the
# forward map F(p) = p + Phi(p); finalize needs F^-1 at every output pixel. Using a
# second CSRBF fitted the other way round (the old "pull field") is only an
# APPROXIMATE inverse -- exact at the control points, but drifting between them by
# up to ~1 atlas voxel at the largest legal displacement, which is exactly the
# preview-vs-labels disagreement this solver removes.
WARP_INV_TOL = 1e-3          # um: forward-residual |F(p)-q| accepted as converged

WARP_INV_MAX_ITER = 40       # Newton iterations before a point is declared stuck

WARP_INV_MIN_DET = 1e-3      # below this |det J| take a fixed-point step instead

WARP_INV_MAX_BAD_FRAC = 1e-4 # abort the finalize if more than this fraction is stuck



def warp_csrbf_invert(src_pts_um, dst_pts_um, R, pts_um, tol=WARP_INV_TOL,
                      max_iter=WARP_INV_MAX_ITER):
    """Solve F(p) = q for p at every point in `pts_um`, where F is the FORWARD
    Wendland-CSRBF field defined by src->dst (the same field warp_csrbf_points and
    the browser's forwardWarp apply). This is the exact inverse of the preview, up
    to `tol`, rather than a second approximating RBF.

    Newton's method on res(p) = p + Phi(p) - q, with J = I + dPhi/dp available
    analytically. Where |det J| is too small to trust (a near-degenerate patch that
    validate_warp_grid's 21x21 sampling could have missed) the step degrades to a
    plain fixed-point update p <- q - Phi(p), which is unconditionally stable while
    ||dPhi|| < 1 and cannot divide by a vanishing determinant.

    Returns (p, stats). `stats` = {iters, n_bad, max_resid, p95_resid, tol} where
    n_bad counts points still above `tol`; those points keep their best iterate
    (a bounded fallback, never a NaN) and the CALLER decides whether the count is
    acceptable -- see finalize_composite / WARP_INV_MAX_BAD_FRAC.
    """
    src = np.asarray(src_pts_um, float).reshape(-1, 2)
    dst = np.asarray(dst_pts_um, float).reshape(-1, 2)
    q = np.asarray(pts_um, float).reshape(-1, 2)
    # identity warp: the inverse is the identity, bit-for-bit
    if len(src) < 1 or R <= 0 or np.allclose(src, dst):
        return q.copy(), {"iters": 0, "n_bad": 0, "max_resid": 0.0,
                          "p95_resid": 0.0, "tol": float(tol)}
    w = _csrbf_weights(src, dst - src, R)      # FORWARD weights, as in the preview
    p = q.copy()
    nr = np.full(len(q), np.inf)
    iters = 0
    converged = False
    for iters in range(1, int(max_iter) + 1):
        disp, jac = _csrbf_field_and_jac(src, w, R, p)
        res = p + disp - q                     # F(p) - q
        nr = np.linalg.norm(res, axis=1)
        if nr.max() <= tol:
            converged = True
            break
        J = jac.copy()                         # J = I + dPhi/dp
        J[:, 0, 0] += 1.0
        J[:, 1, 1] += 1.0
        det = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
        ok = np.abs(det) > WARP_INV_MIN_DET
        step = res.copy()                      # fixed-point step where Newton is unsafe
        if ok.any():                           # Newton where the Jacobian is healthy
            d = det[ok]
            r0 = res[ok, 0]; r1 = res[ok, 1]
            step[ok, 0] = (J[ok, 1, 1] * r0 - J[ok, 0, 1] * r1) / d
            step[ok, 1] = (-J[ok, 1, 0] * r0 + J[ok, 0, 0] * r1) / d
        p = p - step
    if not converged:
        # the last update moved p, so re-measure to report the residual of what we
        # are actually returning rather than of the previous iterate
        disp = _csrbf_eval(src, w, R, p)
        nr = np.linalg.norm(p + disp - q, axis=1)
    return p, {"iters": int(iters), "n_bad": int((nr > tol).sum()),
               "max_resid": float(nr.max()), "p95_resid": float(np.percentile(nr, 95)),
               "tol": float(tol)}



# ===========================================================================
# Phase 2 — sparse warp GRID on top of the global affine (Manual Curation).
#
# Transform order (SINGLE source of truth, mirrored in web/frontend/src/warp.js):
#     atlas microns --M(affine)--> aff microns --W_fwd(CSRBF)--> displayed microns
#
# The control points live in the DISPLAYED plane-micron extent
# [lr0, lr1] x [si0, si1] (affine-independent): ``src`` = the regular node lattice,
# ``dst`` = the user-dragged nodes. The GRID BORDER nodes are held fixed; only the
# interior nodes move. The FORWARD field (centers = src) warps the atlas overlay for
# the browser preview; the backend finalize inverts THAT SAME field NUMERICALLY
# (warp_csrbf_invert: Newton on res(p) = p + Phi(p) - q) at every output pixel, so
# the preview boundaries and the final label raster coincide to the solver tolerance
# rather than to the drift of a second approximating RBF. An identity grid
# (dst == src) makes the inverse the identity, reducing warp_composite EXACTLY to
# warp_affine.
#
# depth-2 raster / fill / boundaries are PREVIEW-ONLY; the confirmed labels are
# always the full LEAF-level annotation resampled by affine + CSRBF.
# ===========================================================================
WARP_GRID_ALGO = "wendland_csrbf_grid_v1"

# DEFAULT grid for new curations. 13x13 = 121 freely draggable interior nodes plus
# 44 edge-constrained nodes for irregular per-region adjustment. rows/cols are
# carried in every persisted warp, so a job
# saved with a 5x5 grid still validates and restores at 5x5 -- these are defaults,
# not assumptions (see warp_grid_nodes / validate_warp_grid, which take rows/cols).
# Dimensions above 15 are rejected before geometric work to bound request cost.
WARP_GRID_ROWS = 13

WARP_GRID_COLS = 13

# Compatibility aliases retained for older callers that named the fine preset.
WARP_GRID_FINE_ROWS = 13

WARP_GRID_FINE_COLS = 13

WARP_GRID_MAX_DIM = 15

# R = factor * mean node spacing. Keeping the factor fixed preserves the same
# support overlap as the lattice densifies while making each pull more local.
# because R follows the spacing, the support overlap is unchanged (R/spacing 1.19 on
# LR and 2.03 on SI, so a node still reaches its neighbours in both axes and the
# lattice deforms continuously), while the halved spacing localises a single-node
# drag from 46.7% of the slice down to 25.8%. Dropping to 1.25 would put R BELOW the
# LR spacing (0.99) and decouple neighbouring nodes into isolated bumps.
WARP_GRID_RADIUS_FACTOR = 1.5

# Node classes. The outer ring used to be pinned wholesale, which meant a user could
# not nudge the tissue outline at all AND an interior node next to the ring fought a
# hard boundary condition, spiking the local gradient into an invalid warp. Now only
# the 4 corners are truly fixed; the rest of the ring slides ALONG the edge it sits
# on, so the outline can be corrected without the box changing shape.
WARP_NODE_CORNER = 0      # fixed

WARP_NODE_EDGE_V = 1      # top/bottom edge: only si (vertical) may change

WARP_NODE_EDGE_H = 2      # left/right edge: only lr (horizontal) may change

WARP_NODE_FREE = 3        # interior: free 2D

WARP_EDGE_DISP_FRAC = 0.6  # edge-node cap, as a fraction of the interior cap

WARP_MAX_DISP_FRAC = 0.45         # max |dst-src| per node, as a fraction of the

#                                   smaller node spacing (also stops neighbours
#                                   from being crossed). Being a FRACTION, the cap
#                                   tightens with the denser grid on its own:
#                                   754um at 5x5 -> 503um at 7x7 on this section.
# A warp may carry its OWN disp_frac to buy range for drastic edits (the elastic
# push tool). It is clamped to this ceiling server-side; a warp that omits the
# field keeps WARP_MAX_DISP_FRAC exactly, so every grid authored by the affine,
# direct-drag or node tools is unaffected.
#
# Why 3.0 and not something tidier: the cap is a CONVENIENCE, not a safety
# property. What actually protects the label raster is gate (3), node ordering,
# and gate (4), the Jacobian determinant. Measured on an 11650 x 6850 um section
# with an elastic push on a 4 x 2.4 mm region, ignoring the cap entirely, the
# fold gate is what fires first in every case -- node crossing never did:
#
#     lattice   max valid push (si / lr)   limiting gate   disp_frac needed
#     13x13         1700 / 700 um          fold            2.98 / 1.23
#      9x9          2400 / 1000 um         fold            2.80 / 1.17
#      7x7          2800 / 1300 um         fold            2.45 / 1.14
#
# A ceiling of 3.0 therefore hands the decision to the Jacobian gate, which is
# the one that means something, instead of cutting the tool off early with an
# arbitrary number. (At 0.9 the same 13x13 push stopped at 514 um with the
# Jacobian still at 0.48, i.e. ten times the 0.05 fold threshold -- all headroom
# and no reason.) Above ~0.52 node crossing becomes reachable for the first time,
# so gate (3) stops being slack and starts doing real work.
#
# lr is consistently ~2.4x more constrained than si because the top and bottom
# edge nodes may only move vertically, so they cannot follow a horizontal push
# and the shear piles up against them.
WARP_MAX_DISP_FRAC_CEILING = 3.0

# LAYERED WARPS. A curation may stack several lattices, applied in order, instead
# of forcing one lattice to carry the whole correction. The user bakes the
# current grid into the stack and keeps dragging on a fresh identity grid.
#
# This is not a way around the fold gate, it is a better-conditioned way to reach
# the same deformation. Every layer is validated on its own, and finalize inverts
# them ONE AT A TIME in reverse, so Newton only ever has to invert a gentle field.
# Measured, composing layers each at the full single-layer cap on a 13x13
# lattice, inverting the chain exactly as finalize does:
#
#     layers   total move   composed min|J|   Newton inverse
#        1        306 um         0.610        n_bad 0, resid 1e-6 um
#        3        668 um         0.260        n_bad 0, resid 1e-6 um
#        6        871 um         0.121        n_bad 0, resid 9e-6 um
#
# A SINGLE warp of comparable deformation does not survive that: the recorded
# failures are n_bad 16-24 at det 0.082-0.335, which is what WARP_JAC_MIN_BOOSTED
# exists to keep away from. Six small warps each have a wide Newton basin; one
# big one has none.
#
# Returns diminish -- 306, +226, +136, +89, +64, +49 -- because the CSRBF bump
# stays centred on its lattice node while the tissue moves away from it. The cap
# is therefore about bounding cost and drift, not about rationing a budget:
# finalize pays one more inversion pass over the output raster per layer.
WARP_MAX_LAYERS = 8

# Floor on the determinant of the COMPOSED map, sampled over the plane.
# Per-layer validation is not sufficient on its own: eight individually legal
# layers can still multiply into something extreme, since det composes as a
# product. Deliberately looser than WARP_JAC_MIN_BOOSTED because the measurement
# above shows a chain inverts cleanly well below it -- what makes a single warp
# dangerous is the narrow Newton basin, and a chain does not have one.
WARP_COMPOSED_JAC_MIN = 0.10

WARP_JAC_MIN = 0.05               # reject fold / extreme compression (det <= this)

# Stricter fold floor for a warp that opted into a raised disp_frac. Not taste --
# WARP_JAC_MIN was calibrated when no warp could displace more than ~257 um, and at
# that range the map never came near it. With the budget raised a grid can sit right
# on det = 0.05, be ACCEPTED here, and then have warp_csrbf_invert fail to converge
# in finalize_composite: the job dies AFTER the user confirms, which is the worst
# possible place to find out. Measured against the real inverse over a 31921-pixel
# output grid, sweeping elastic pushes on a 13x13 lattice:
#
#     direction   min det   pixels the Newton inverse misses
#     vertical     0.243                  0          clean
#     vertical     0.136                  2          marginal
#     vertical     0.082                 16          FAILS finalize
#     horizontal   0.468                  0          clean
#     horizontal   0.335                 24          FAILS finalize
#
# Note the determinant is NOT a good predictor on its own: horizontal pushes break
# at det 0.335 while vertical ones survive 0.243, because the top/bottom edge nodes
# may only move vertically, so a horizontal push piles anisotropic shear against
# them -- det stays up while one axis compresses hard and Newton's basin shrinks.
# 0.50 is the floor at which BOTH axes invert exactly (n_bad = 0, residuals ~1e-8 um)
# at every lattice size tested. A tighter, more principled gate would check inverse
# convergence directly instead of inferring it from det; that is a bigger change
# because the browser mirror would have to run the same probe.
#
# Warps that do not raise disp_frac keep WARP_JAC_MIN exactly, so nothing already
# accepted changes verdict.
WARP_JAC_MIN_BOOSTED = 0.50

WARP_JAC_MAX = 20.0               # reject extreme local stretch  (det >= this)

WARP_JAC_SAMPLES = 21             # FLOOR for the NxN forward-Jacobian sampling grid

WARP_JAC_PER_CELL = 5             # samples per lattice cell along each axis

WARP_JAC_EPS = 1.0                # micron step for the central-difference Jacobian



def warp_jac_samples(rows=WARP_GRID_ROWS, cols=WARP_GRID_COLS):
    """N for the NxN Jacobian sampling grid, scaled so samples-per-lattice-cell stays
    constant as the grid densifies. A fixed 21 was 5.25 samples/cell at 5x5 but only
    3.5 at 7x7, where it already under-read fold severity (-0.159 vs -0.193 on a
    201x201 reference). This returns 21 for 5x5 -- so previously-validated 5x5 warps
    are judged by the exact same numbers -- and 31 for 7x7."""
    return int(max(WARP_JAC_SAMPLES,
                   WARP_JAC_PER_CELL * (max(int(rows), int(cols)) - 1) + 1))



def warp_grid_extent(bundle):
    """Displayed plane-micron extent (lr0, lr1, si0, si1) the warp grid spans.
    Identical to the ``lr0/lr1/si0/si1`` of the frontend bundle meta."""
    xJ0, xJ1, _dSI, _dLR = _plane_spacing(bundle)
    return float(xJ1[0]), float(xJ1[-1]), float(xJ0[0]), float(xJ0[-1])



def warp_grid_nodes(bundle, rows=WARP_GRID_ROWS, cols=WARP_GRID_COLS):
    """Regular rows x cols control-point lattice over the displayed extent.
    Returns [rows*cols, 2] plane-micron [lr, si] nodes, row-major (si outer, lr
    inner) -- identical ordering to the frontend ``gridNodes()``."""
    lr0, lr1, si0, si1 = warp_grid_extent(bundle)
    lrs = np.linspace(lr0, lr1, cols)
    sis = np.linspace(si0, si1, rows)
    return np.array([[lr, si] for si in sis for lr in lrs], float)



def warp_grid_radius(bundle, rows=WARP_GRID_ROWS, cols=WARP_GRID_COLS):
    """CSRBF support radius for the grid = factor * mean node spacing (microns)."""
    lr0, lr1, si0, si1 = warp_grid_extent(bundle)
    sp_lr = (lr1 - lr0) / (cols - 1); sp_si = (si1 - si0) / (rows - 1)
    return WARP_GRID_RADIUS_FACTOR * 0.5 * (sp_lr + sp_si)



def warp_layers(warp):
    """Normalise any warp payload to an ordered list of lattice layers.

    Three shapes reach this: ``None``, a single ``{src, dst, ...}`` lattice as
    every warp confirmed before layering existed, and ``{"layers": [...]}``.
    Collapsing them here means every consumer -- validation, finalize, the
    transform endpoint -- sees one shape, and a job saved last month keeps
    working as a chain of length one.
    """
    if not warp:
        return []
    if isinstance(warp, (list, tuple)):
        layers = list(warp)
    elif isinstance(warp, dict) and warp.get("layers"):
        layers = list(warp["layers"])
    else:
        layers = [warp]
    return [ly for ly in layers
            if isinstance(ly, dict) and ly.get("src") and ly.get("dst")]


def warp_max_disp(extent, rows=WARP_GRID_ROWS, cols=WARP_GRID_COLS,
                  disp_frac=None):
    """Per-node displacement cap in MICRONS for a grid over ``extent``.

    ``disp_frac`` is the warp's own requested fraction of the smaller node
    spacing. ``None`` (the normal case) yields the historical
    WARP_MAX_DISP_FRAC. Anything else is clamped into
    (0, WARP_MAX_DISP_FRAC_CEILING] here, so a hand-crafted POST cannot buy
    itself an unbounded budget -- callers pass the result straight to
    validate_warp_grid's ``max_disp``. Mirrored by ``maxDisp`` in
    web/frontend/src/warp.js."""
    lr0, lr1, si0, si1 = extent
    sp_lr = (lr1 - lr0) / (int(cols) - 1)
    sp_si = (si1 - si0) / (int(rows) - 1)
    frac = WARP_MAX_DISP_FRAC if disp_frac is None else float(disp_frac)
    if not np.isfinite(frac) or frac <= 0:
        frac = WARP_MAX_DISP_FRAC
    frac = min(frac, WARP_MAX_DISP_FRAC_CEILING)
    return frac * min(sp_lr, sp_si)



def warp_grid_edge_mask(rows=WARP_GRID_ROWS, cols=WARP_GRID_COLS):
    """Boolean [rows*cols] -- True where a node sits on the grid BORDER.

    Border no longer means immovable: see warp_node_kinds. Only the 4 corners are
    fixed; the other border nodes slide along their own edge."""
    m = np.zeros((rows, cols), bool)
    m[0, :] = m[-1, :] = m[:, 0] = m[:, -1] = True
    return m.ravel()



def warp_node_kinds(rows=WARP_GRID_ROWS, cols=WARP_GRID_COLS):
    """int [rows*cols] of WARP_NODE_* -- what each lattice node is allowed to do.

    corners            -> WARP_NODE_CORNER (fixed)
    top / bottom edge  -> WARP_NODE_EDGE_V (si only; lr pinned)
    left / right edge  -> WARP_NODE_EDGE_H (lr only; si pinned)
    everything else    -> WARP_NODE_FREE   (2D)

    Row-major (si outer, lr inner), matching warp_grid_nodes and the frontend's
    nodeKinds(). For 13x13 that is 4 fixed, 44 edge-constrained and 121 free = 165
    movable nodes."""
    k = np.full((rows, cols), WARP_NODE_FREE, int)
    k[0, :] = k[-1, :] = WARP_NODE_EDGE_V      # top/bottom slide vertically
    k[:, 0] = k[:, -1] = WARP_NODE_EDGE_H      # left/right slide horizontally
    for r in (0, rows - 1):
        for c in (0, cols - 1):
            k[r, c] = WARP_NODE_CORNER
    return k.ravel()



def warp_movable_count(rows=WARP_GRID_ROWS, cols=WARP_GRID_COLS):
    """How many nodes the user can move at all (everything but the 4 corners)."""
    return int(rows) * int(cols) - 4



def _warp_jacobian_dets(src, dst, R, pts, eps=WARP_JAC_EPS):
    """det of the FORWARD CSRBF Jacobian at each point (central difference on the
    exact forward field used by the browser preview). >0 everywhere => no fold."""
    src = np.asarray(src, float).reshape(-1, 2)
    dst = np.asarray(dst, float).reshape(-1, 2)
    pts = np.asarray(pts, float).reshape(-1, 2)
    ex = np.array([eps, 0.0]); ey = np.array([0.0, eps])
    dFdx = (warp_csrbf_points(src, dst, R, pts + ex) -
            warp_csrbf_points(src, dst, R, pts - ex)) / (2.0 * eps)
    dFdy = (warp_csrbf_points(src, dst, R, pts + ey) -
            warp_csrbf_points(src, dst, R, pts - ey)) / (2.0 * eps)
    return dFdx[:, 0] * dFdy[:, 1] - dFdx[:, 1] * dFdy[:, 0]



def warp_jac_min(disp_frac=None):
    """Fold floor for a warp. A raised disp_frac buys range but must also accept
    the stricter floor the finalize inverse needs -- see WARP_JAC_MIN_BOOSTED."""
    return WARP_JAC_MIN if disp_frac is None else WARP_JAC_MIN_BOOSTED



def validate_warp_grid(src, dst, R, extent, rows=WARP_GRID_ROWS,
                       cols=WARP_GRID_COLS, max_disp=None, jac_min=None):
    """Guard a sparse warp grid (mirrored in web/frontend/src/warp.js). BOTH the
    front-end preview AND this backend finalize must pass it before a warp is
    accepted -- the backend never trusts the client's grid. Returns a dict with
    ``ok``, ``reason``, ``min_det``, ``max_det``, ``max_disp``, ``n_moved``."""
    rows, cols = int(rows), int(cols)
    if rows < 3 or cols < 3 or rows > WARP_GRID_MAX_DIM or cols > WARP_GRID_MAX_DIM:
        return {"ok": False, "reason": "unsupported grid dimensions",
                "min_det": None, "max_det": None, "max_disp": None,
                "n_moved": 0}
    src = np.asarray(src, float).reshape(-1, 2)
    dst = np.asarray(dst, float).reshape(-1, 2)
    lr0, lr1, si0, si1 = extent
    sp_lr = (lr1 - lr0) / (cols - 1); sp_si = (si1 - si0) / (rows - 1)
    if max_disp is None:
        max_disp = WARP_MAX_DISP_FRAC * min(sp_lr, sp_si)
    if len(src) != rows * cols or dst.shape != src.shape:
        return {"ok": False, "reason": "grid shape mismatch", "min_det": None,
                "max_det": None, "max_disp": None, "n_moved": 0}
    kinds = warp_node_kinds(rows, cols)
    d = dst - src
    disp = np.linalg.norm(d, axis=1)
    moved = disp > 1e-6
    n_moved = int(moved.sum())

    def bad(reason):
        return {"ok": False, "reason": reason, "min_det": None, "max_det": None,
                "max_disp": float(disp.max()), "n_moved": n_moved}

    # (1) per-node degrees of freedom. Enforced HERE, not just in the browser: a
    #     hand-crafted POST must not be able to slide a corner or push an edge node
    #     off its own edge (which would deform the sampling box itself).
    if np.any(moved & (kinds == WARP_NODE_CORNER)):
        return bad("corner control point moved")
    if np.any((np.abs(d[:, 0]) > 1e-6) & (kinds == WARP_NODE_EDGE_V)):
        return bad("top/bottom edge node moved horizontally")
    if np.any((np.abs(d[:, 1]) > 1e-6) & (kinds == WARP_NODE_EDGE_H)):
        return bad("left/right edge node moved vertically")

    # (2) displacement cap -- tighter on the edge than inside, so a boundary tweak
    #     can never out-run the interior nodes it has to stay consistent with.
    lim = np.where(kinds == WARP_NODE_FREE, max_disp,
                   WARP_EDGE_DISP_FRAC * max_disp)
    if np.any(disp > lim + 1e-6):
        return bad("control-point displacement exceeds limit")

    # (3) node ordering: lr must stay increasing along every row and si along every
    #     column, i.e. no node may cross a neighbour. At the default cap this is
    #     unreachable (2 x 0.45 < 1.0 spacing) and acts as a backstop against a
    #     malformed payload. It is LOAD-BEARING once a warp raises its own
    #     disp_frac (see WARP_MAX_DISP_FRAC_CEILING): at 0.9 two adjacent nodes can
    #     close 1.8x their spacing, so this is the check that stops the lattice
    #     inverting, with the Jacobian gate below covering folds in the field.
    LR = dst[:, 0].reshape(rows, cols)
    SI = dst[:, 1].reshape(rows, cols)
    if np.any(np.diff(LR, axis=1) <= 0) or np.any(np.diff(SI, axis=0) <= 0):
        return bad("grid nodes crossed a neighbour")

    if not moved.any():                                      # identity grid
        return {"ok": True, "reason": "identity", "min_det": 1.0, "max_det": 1.0,
                "max_disp": 0.0, "n_moved": 0}
    N = warp_jac_samples(rows, cols)                         # (4) no fold / stretch
    gx = np.linspace(lr0, lr1, N); gy = np.linspace(si0, si1, N)
    P = np.array([[x, y] for y in gy for x in gx], float)
    dets = _warp_jacobian_dets(src, dst, R, P)
    mn = float(dets.min()); mx = float(dets.max())
    jmin = WARP_JAC_MIN if jac_min is None else float(jac_min)
    if mn <= jmin or mx >= WARP_JAC_MAX:
        return {"ok": False,
                "reason": (f"warp folds or over-stretches "
                           f"(Jacobian det in [{mn:.3f}, {mx:.3f}])"),
                "min_det": mn, "max_det": mx,
                "max_disp": float(disp.max()), "n_moved": n_moved}
    return {"ok": True, "reason": "ok", "min_det": mn, "max_det": mx,
            "max_disp": float(disp.max()), "n_moved": n_moved}
