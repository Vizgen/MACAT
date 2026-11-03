"""Unit tests for the Phase 2 sparse warp grid (pipeline_api).

Covers the two backend pieces the browser must never be trusted to get right:

  validate_warp_grid  — the three guards that decide whether a client's grid is
      acceptable at all: border nodes fixed, per-node displacement cap, and a
      forward-Jacobian check that rejects folds/over-stretch. The cap alone is
      NOT sufficient (two adjacent nodes can each stay under it and still fold
      the map), so the Jacobian guard is load-bearing — test_fold_within_cap
      pins that down.

  finalize_composite  — that an identity/absent grid reduces EXACTLY to the pure
      global-affine finalize, that a real grid changes labels, and that an
      invalid grid is rejected by the backend even though the client already
      "validated" it.

The grid-shape tests are pure math and need no data. The composite tests need a
registration bundle and are skipped when none is on disk.

Written with stdlib unittest (no pytest in this env). Run:

  OMP_NUM_THREADS=1 python -m unittest tests.test_warp_grid -v
"""
import glob
import os
import sys
import unittest

_pre = os.path.join(sys.prefix, "lib", "libstdc++.so.6")
if os.path.exists(_pre) and _pre not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = _pre + (
        ":" + os.environ["LD_PRELOAD"] if os.environ.get("LD_PRELOAD") else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import numpy as np                        # noqa: E402
import config                            # noqa: E402
import pipeline_api as api                # noqa: E402

ROWS, COLS = api.WARP_GRID_ROWS, api.WARP_GRID_COLS
CENTRE = (ROWS // 2) * COLS + COLS // 2
# a synthetic extent of the same order as a real coronal section (microns)
EXTENT = (-5775.0, 5775.0, -3475.0, 3475.0)


def _lattice(extent=EXTENT):
    """The same lattice warp_grid_nodes builds, without needing a bundle."""
    lr0, lr1, si0, si1 = extent
    lrs = np.linspace(lr0, lr1, COLS)
    sis = np.linspace(si0, si1, ROWS)
    src = np.array([[lr, si] for si in sis for lr in lrs], float)
    sp_lr = (lr1 - lr0) / (COLS - 1)
    sp_si = (si1 - si0) / (ROWS - 1)
    R = api.WARP_GRID_RADIUS_FACTOR * 0.5 * (sp_lr + sp_si)
    cap = api.WARP_MAX_DISP_FRAC * min(sp_lr, sp_si)
    return src, R, cap


def _moved(src, moves):
    dst = src.copy()
    for i, (dlr, dsi) in moves.items():
        dst[i] = dst[i] + [dlr, dsi]
    return dst


def _fold_pair(extent=EXTENT):
    """Two adjacent interior nodes whose convergence folds the field, plus the
    axis to push them along.

    The per-node displacement cap is a fraction of the SMALLER node spacing, so a
    fold is only reachable along the tighter axis: pushing two nodes together
    across the wider spacing cannot close enough of the gap. Returns
    (i, j, axis) where i moves in +axis and j in -axis.
    """
    lr0, lr1, si0, si1 = extent
    sp_lr = (lr1 - lr0) / (COLS - 1)
    sp_si = (si1 - si0) / (ROWS - 1)
    if sp_lr <= sp_si:
        return CENTRE - 1, CENTRE, 0        # horizontally adjacent, push along lr
    return CENTRE - COLS, CENTRE, 1         # vertically adjacent, push along si


def _find_bundle():
    # only a bundle at the configured resolution; see tests/_bundles.py
    from _bundles import first_bundle
    return first_bundle()


# ------------------------------------------------------- validate_warp_grid
class TestValidateWarpGrid(unittest.TestCase):
    def setUp(self):
        self.src, self.R, self.cap = _lattice()

    def test_identity_accepted(self):
        """An untouched grid is valid, reports no movement and unit Jacobian."""
        v = api.validate_warp_grid(self.src, self.src.copy(), self.R, EXTENT)
        self.assertTrue(v["ok"])
        self.assertEqual(v["reason"], "identity")
        self.assertEqual(v["n_moved"], 0)
        self.assertEqual(v["max_disp"], 0.0)
        self.assertEqual(v["min_det"], 1.0)

    def test_interior_node_accepted(self):
        """A modest interior-node move is accepted with a positive Jacobian."""
        dx, dy = self.cap * 0.50, self.cap * 0.25
        dst = _moved(self.src, {CENTRE: (dx, dy)})
        v = api.validate_warp_grid(self.src, dst, self.R, EXTENT)
        self.assertTrue(v["ok"], v["reason"])
        self.assertEqual(v["n_moved"], 1)
        self.assertGreater(v["min_det"], api.WARP_JAC_MIN)
        self.assertAlmostEqual(v["max_disp"], float(np.hypot(dx, dy)), places=6)

    def test_corner_nodes_are_fixed(self):
        """The 4 corners are the only immovable nodes."""
        kinds = api.warp_node_kinds(ROWS, COLS)
        idx = [i for i in range(len(self.src)) if kinds[i] == api.WARP_NODE_CORNER]
        self.assertEqual(len(idx), 4)
        for i in idx:
            for mv in ((60.0, 0.0), (0.0, 60.0), (40.0, 40.0)):
                v = api.validate_warp_grid(self.src, _moved(self.src, {i: mv}),
                                           self.R, EXTENT)
                self.assertFalse(v["ok"], f"corner {i} moved by {mv}")
                self.assertEqual(v["reason"], "corner control point moved")

    def test_edge_nodes_slide_along_their_own_edge_only(self):
        """top/bottom move vertically, left/right horizontally; the off-axis
        component is rejected by the BACKEND, not just clamped in the browser."""
        kinds = api.warp_node_kinds(ROWS, COLS)
        cap_edge = api.WARP_EDGE_DISP_FRAC * self.cap
        step = cap_edge * 0.5
        nv = nh = 0
        for i, k in enumerate(kinds):
            if k == api.WARP_NODE_EDGE_V:                # only si may change
                nv += 1
                ok = api.validate_warp_grid(self.src, _moved(self.src, {i: (0.0, step)}),
                                            self.R, EXTENT)
                self.assertTrue(ok["ok"], f"node {i} vertical: {ok['reason']}")
                bad = api.validate_warp_grid(self.src, _moved(self.src, {i: (step, 0.0)}),
                                             self.R, EXTENT)
                self.assertFalse(bad["ok"])
                self.assertEqual(bad["reason"], "top/bottom edge node moved horizontally")
            elif k == api.WARP_NODE_EDGE_H:              # only lr may change
                nh += 1
                ok = api.validate_warp_grid(self.src, _moved(self.src, {i: (step, 0.0)}),
                                            self.R, EXTENT)
                self.assertTrue(ok["ok"], f"node {i} horizontal: {ok['reason']}")
                bad = api.validate_warp_grid(self.src, _moved(self.src, {i: (0.0, step)}),
                                             self.R, EXTENT)
                self.assertFalse(bad["ok"])
                self.assertEqual(bad["reason"], "left/right edge node moved vertically")
        self.assertEqual(nv + nh, 2 * (ROWS + COLS) - 8)

    def test_edge_cap_is_tighter_than_interior(self):
        """An edge node may not travel as far as an interior one."""
        kinds = api.warp_node_kinds(ROWS, COLS)
        e = next(i for i, k in enumerate(kinds) if k == api.WARP_NODE_EDGE_V)
        cap_edge = api.WARP_EDGE_DISP_FRAC * self.cap
        self.assertLess(cap_edge, self.cap)
        ok = api.validate_warp_grid(self.src, _moved(self.src, {e: (0.0, cap_edge * 0.95)}),
                                    self.R, EXTENT)
        self.assertTrue(ok["ok"], ok["reason"])
        bad = api.validate_warp_grid(self.src, _moved(self.src, {e: (0.0, cap_edge * 1.10)}),
                                     self.R, EXTENT)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "control-point displacement exceeds limit")
        # ... and that same displacement IS allowed for an interior node
        f = next(i for i, k in enumerate(kinds) if k == api.WARP_NODE_FREE)
        self.assertTrue(api.validate_warp_grid(
            self.src, _moved(self.src, {f: (0.0, cap_edge * 1.10)}),
            self.R, EXTENT)["ok"])

    def test_neighbour_crossing_rejected(self):
        """A payload that reorders nodes is refused even if every cap is respected."""
        dst = self.src.copy()
        i = (ROWS // 2) * COLS + COLS // 2
        dst[[i, i + 1]] = dst[[i + 1, i]]        # swap two nodes in a row
        v = api.validate_warp_grid(self.src, dst, self.R, EXTENT)
        self.assertFalse(v["ok"])

    def test_interior_count(self):
        """Exactly (ROWS-2)*(COLS-2) nodes are draggable — 9 for a 5x5 grid."""
        edge = api.warp_grid_edge_mask()
        self.assertEqual(int((~edge).sum()), (ROWS - 2) * (COLS - 2))

    def test_displacement_cap(self):
        """Just under the per-node cap passes; just over it is rejected."""
        ok = api.validate_warp_grid(
            self.src, _moved(self.src, {CENTRE: (self.cap * 0.98, 0.0)}),
            self.R, EXTENT)
        self.assertTrue(ok["ok"], ok["reason"])
        bad = api.validate_warp_grid(
            self.src, _moved(self.src, {CENTRE: (self.cap * 1.02, 0.0)}),
            self.R, EXTENT)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "control-point displacement exceeds limit")

    def test_fold_within_cap(self):
        """The Jacobian guard is load-bearing, not redundant with the cap.

        Two adjacent interior nodes driven into each other along the grid's
        tightest axis are BOTH within the per-node displacement cap, yet the
        composed field folds (negative Jacobian determinant). Only the Jacobian
        check catches this.
        """
        i, j, ax = _fold_pair()
        step = [0.0, 0.0]; step[ax] = self.cap * 0.999
        dst = _moved(self.src, {i: tuple(step),
                                j: tuple(-v for v in step)})
        disp = np.linalg.norm(dst - self.src, axis=1)
        self.assertLessEqual(float(disp.max()), self.cap + 1e-6,
                             "test premise: every node must stay under the cap")
        v = api.validate_warp_grid(self.src, dst, self.R, EXTENT)
        self.assertFalse(v["ok"])
        self.assertIn("folds", v["reason"])
        self.assertLess(v["min_det"], 0.0)

    def test_shape_mismatch_rejected(self):
        """A grid of the wrong length is rejected before any geometry runs."""
        v = api.validate_warp_grid(self.src[:-1], self.src[:-1].copy(),
                                   self.R, EXTENT)
        self.assertFalse(v["ok"])
        self.assertEqual(v["reason"], "grid shape mismatch")

    def test_jacobian_at_nodes_matches_displacement(self):
        """The forward field interpolates the moved node (F(src_i) == dst_i)."""
        dst = _moved(self.src, {CENTRE: (250.0, -175.0)})
        out = api.warp_csrbf_points(self.src, dst, self.R, self.src)
        self.assertLess(float(np.abs(out - dst).max()), 1e-3)


# ------------------------------------------------------- warp / finalize composite
@unittest.skipIf(_find_bundle() is None, "no registration bundle at the configured "
                 "resolution; see tests/_bundles.py")
class TestComposite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = _find_bundle()
        cls.b = api.load_bundle(cls.path)
        cls.ext = api.warp_grid_extent(cls.b)
        cls.src = api.warp_grid_nodes(cls.b)
        cls.R = api.warp_grid_radius(cls.b)
        lr0, lr1, si0, si1 = cls.ext
        cls.cap = api.WARP_MAX_DISP_FRAC * min(
            (lr1 - lr0) / (COLS - 1), (si1 - si0) / (ROWS - 1))
        cls.M = api.affine_from_params((lr0 + lr1) / 2, (si0 + si1) / 2,
                                       120.0, -80.0, 4.0, 1.05, 0.97)

    def test_grid_matches_extent(self):
        """The lattice spans exactly the displayed extent the frontend is given."""
        self.assertEqual(len(self.src), ROWS * COLS)
        self.assertAlmostEqual(self.src[:, 0].min(), self.ext[0], places=6)
        self.assertAlmostEqual(self.src[:, 0].max(), self.ext[1], places=6)
        self.assertAlmostEqual(self.src[:, 1].min(), self.ext[2], places=6)
        self.assertAlmostEqual(self.src[:, 1].max(), self.ext[3], places=6)

    def test_identity_grid_equals_pure_affine(self):
        """dst == src must reduce warp_composite EXACTLY to warp_affine.

        This is the invariant that lets one finalize path serve both the
        pure-affine and the warped case without a behaviour change for users who
        never touch the grid.
        """
        A_aff, tf_aff, rid_aff = api.warp_affine(self.b, self.M)
        A_id, tf_id, rid_id, inv = api.warp_composite(self.b, self.M, self.src,
                                                      self.src.copy(), self.R)
        self.assertEqual(inv["iters"], 0, "identity must not iterate at all")
        self.assertEqual(inv["n_bad"], 0)
        self.assertTrue(np.array_equal(A_aff, A_id), "label raster differs")
        self.assertTrue(np.array_equal(rid_aff, rid_id), "per-cell labels differ")
        self.assertTrue(np.allclose(tf_aff, tf_id), "CCF coordinate field differs")

    def test_moved_grid_changes_labels(self):
        """A legal grid actually deforms the raster and relabels cells."""
        dst = self.src.copy()
        dst[CENTRE] = dst[CENTRE] + [self.cap * 0.50, self.cap * 0.25]
        self.assertTrue(api.validate_warp_grid(self.src, dst, self.R, self.ext)["ok"])
        A_aff, _, rid_aff = api.warp_affine(self.b, self.M)
        A_w, _, rid_w, inv = api.warp_composite(self.b, self.M, self.src, dst, self.R)
        self.assertGreater(int((A_w != A_aff).sum()), 0)
        self.assertGreater(int((rid_w != rid_aff).sum()), 0)
        self.assertEqual(inv["n_bad"], 0, "every output pixel must invert")
        self.assertLessEqual(inv["max_resid"], api.WARP_INV_TOL)

    def test_composite_is_deterministic(self):
        """Same affine + same grid -> bit-identical labels (re-runnable finalize)."""
        dst = self.src.copy()
        dst[CENTRE] = dst[CENTRE] + [180.0, -120.0]
        _, _, r1, _ = api.warp_composite(self.b, self.M, self.src, dst, self.R)
        _, _, r2, _ = api.warp_composite(self.b, self.M, self.src, dst, self.R)
        self.assertTrue(np.array_equal(r1, r2))

    def test_finalize_composite_rejects_invalid_grid(self):
        """The backend re-validates: a folded grid raises instead of being applied.

        The browser runs the same check first, but a hand-crafted POST must not be
        able to fold the label raster.
        """
        lr0, lr1, si0, si1 = self.ext
        cap = api.WARP_MAX_DISP_FRAC * min((lr1 - lr0) / (COLS - 1),
                                           (si1 - si0) / (ROWS - 1))
        i, j, ax = _fold_pair(self.ext)
        step = np.zeros(2); step[ax] = cap * 0.999
        dst = self.src.copy()
        dst[i] = dst[i] + step
        dst[j] = dst[j] - step
        affine = {"cx": (lr0 + lr1) / 2, "cy": (si0 + si1) / 2,
                  "tx": 0.0, "ty": 0.0, "rot_deg": 0.0, "sx": 1.0, "sy": 1.0}
        warp = {"algo": api.WARP_GRID_ALGO, "rows": ROWS, "cols": COLS,
                "R": self.R, "src": self.src.tolist(), "dst": dst.tolist()}
        with self.assertRaises(ValueError) as cm:
            api.finalize_composite(self.b, str(self.b["exp_path"]), affine, warp,
                                   out_dir="/dev/null/should-not-be-reached")
        self.assertIn("rejected by backend", str(cm.exception))

    def test_finalize_composite_rejects_off_axis_edge_move(self):
        """A client that slides an edge node OFF its own edge is refused by the
        backend, not merely clamped in the browser."""
        lr0, lr1, si0, si1 = self.ext
        kinds = api.warp_node_kinds(ROWS, COLS)
        e = next(i for i, k in enumerate(kinds) if k == api.WARP_NODE_EDGE_V)
        dst = self.src.copy()
        dst[e] = dst[e] + [80.0, 0.0]            # horizontal move on a vertical edge
        affine = {"cx": (lr0 + lr1) / 2, "cy": (si0 + si1) / 2,
                  "tx": 0.0, "ty": 0.0, "rot_deg": 0.0, "sx": 1.0, "sy": 1.0}
        warp = {"algo": api.WARP_GRID_ALGO, "rows": ROWS, "cols": COLS,
                "R": self.R, "src": self.src.tolist(), "dst": dst.tolist()}
        with self.assertRaises(ValueError) as cm:
            api.finalize_composite(self.b, str(self.b["exp_path"]), affine, warp,
                                   out_dir="/dev/null/should-not-be-reached")
        self.assertIn("moved horizontally", str(cm.exception))

    def test_finalize_composite_rejects_moved_corner(self):
        """The 4 corners stay fixed at the finalize level too."""
        lr0, lr1, si0, si1 = self.ext
        dst = self.src.copy()
        dst[0] = dst[0] + [80.0, 0.0]
        affine = {"cx": (lr0 + lr1) / 2, "cy": (si0 + si1) / 2,
                  "tx": 0.0, "ty": 0.0, "rot_deg": 0.0, "sx": 1.0, "sy": 1.0}
        warp = {"algo": api.WARP_GRID_ALGO, "rows": ROWS, "cols": COLS,
                "R": self.R, "src": self.src.tolist(), "dst": dst.tolist()}
        with self.assertRaises(ValueError) as cm:
            api.finalize_composite(self.b, str(self.b["exp_path"]), affine, warp,
                                   out_dir="/dev/null/should-not-be-reached")
        self.assertIn("corner control point moved", str(cm.exception))


# ------------------------------------------------- grid size: default and legacy
class TestGridSize(unittest.TestCase):
    """The default grid is 13x13 (121 draggable interior nodes), but rows/cols are DATA:
    a warp saved at 5x5 must keep validating at 5x5, never be reinterpreted on the
    new lattice."""

    def test_default_is_13x13_with_121_interior(self):
        self.assertEqual((ROWS, COLS), (13, 13))
        edge = api.warp_grid_edge_mask(ROWS, COLS)
        self.assertEqual(edge.size, 169)
        self.assertEqual(int(edge.sum()), 48)
        self.assertEqual(int((~edge).sum()), 121)

    def test_jac_sampling_scales_but_keeps_5x5_identical(self):
        """Denser grid -> denser fold sampling, while 5x5 keeps its historical 21 so
        previously-validated 5x5 warps are judged by the same numbers."""
        self.assertEqual(api.warp_jac_samples(5, 5), 21)
        self.assertEqual(api.warp_jac_samples(7, 7), 31)
        self.assertEqual(api.warp_jac_samples(9, 9), 41)
        self.assertEqual(api.warp_jac_samples(13, 13), 61)
        # the absolute sample count grows with the grid ...
        self.assertGreater(api.warp_jac_samples(7, 7), api.warp_jac_samples(5, 5))
        # ... and BOTH keep at least WARP_JAC_PER_CELL samples per lattice cell, which
        # is the property that matters (density is 5 + 1/cells, so a smaller grid is
        # marginally denser per cell -- that is the endpoint, not a regression).
        for n in (5, 7, 9, 13):
            self.assertGreaterEqual(api.warp_jac_samples(n, n) / (n - 1),
                                    api.WARP_JAC_PER_CELL)

    def test_every_interior_node_is_independently_draggable(self):
        """All 121 interior nodes accept a legal drag on their own, and each one is
        reported as the single moved node."""
        src, R, cap = _lattice()
        edge = api.warp_grid_edge_mask(ROWS, COLS)
        interior = [i for i in range(len(src)) if not edge[i]]
        self.assertEqual(len(interior), 121)
        for i in interior:
            dst = _moved(src, {i: (cap * 0.5, cap * 0.3)})
            v = api.validate_warp_grid(src, dst, R, EXTENT, ROWS, COLS)
            self.assertTrue(v["ok"], f"node {i} rejected: {v['reason']}")
            self.assertEqual(v["n_moved"], 1, f"node {i}")
            self.assertGreater(v["min_det"], api.WARP_JAC_MIN, f"node {i}")

    def test_node_class_counts(self):
        """13x13 = 4 fixed corners + 44 edge-constrained + 121 free = 165 movable."""
        k = api.warp_node_kinds(ROWS, COLS)
        self.assertEqual(int((k == api.WARP_NODE_CORNER).sum()), 4)
        self.assertEqual(int((k == api.WARP_NODE_EDGE_V).sum()), 22)
        self.assertEqual(int((k == api.WARP_NODE_EDGE_H).sum()), 22)
        self.assertEqual(int((k == api.WARP_NODE_FREE).sum()), 121)
        self.assertEqual(api.warp_movable_count(ROWS, COLS), 165)

    def test_legacy_5x5_grid_still_validates(self):
        """A 5x5 warp is validated on ITS OWN lattice, with its own cap and radius."""
        lr0, lr1, si0, si1 = EXTENT
        lrs = np.linspace(lr0, lr1, 5)
        sis = np.linspace(si0, si1, 5)
        src5 = np.array([[lr, si] for si in sis for lr in lrs], float)
        sp_lr = (lr1 - lr0) / 4.0
        sp_si = (si1 - si0) / 4.0
        R5 = api.WARP_GRID_RADIUS_FACTOR * 0.5 * (sp_lr + sp_si)
        cap5 = api.WARP_MAX_DISP_FRAC * min(sp_lr, sp_si)
        c5 = 2 * 5 + 2                                  # centre of a 5x5 lattice
        edge5 = api.warp_grid_edge_mask(5, 5)
        self.assertEqual(int((~edge5).sum()), 9)
        dst5 = src5.copy()
        dst5[c5] = dst5[c5] + [cap5 * 0.9, 0.0]
        v = api.validate_warp_grid(src5, dst5, R5, EXTENT, 5, 5)
        self.assertTrue(v["ok"], v["reason"])
        self.assertEqual(v["n_moved"], 1)
        # and the 5x5 cap is genuinely looser than the current one on the same extent
        _src7, _R7, cap7 = _lattice()
        self.assertGreater(cap5, cap7)

    def test_legacy_5x5_shape_not_reinterpreted_as_default(self):
        """Handing a 25-point (5x5) grid to the default validator must be a shape error,
        not a silent reinterpretation -- 25 points happen to be a valid count for
        neither lattice's interior."""
        lr0, lr1, si0, si1 = EXTENT
        lrs = np.linspace(lr0, lr1, 5)
        sis = np.linspace(si0, si1, 5)
        src5 = np.array([[lr, si] for si in sis for lr in lrs], float)
        v = api.validate_warp_grid(src5, src5.copy(), 1000.0, EXTENT, ROWS, COLS)
        self.assertFalse(v["ok"])
        self.assertEqual(v["reason"], "grid shape mismatch")

    def test_fine_13x13_grid_accepts_a_small_interior_drag(self):
        rows = cols = 13
        lr0, lr1, si0, si1 = EXTENT
        lrs = np.linspace(lr0, lr1, cols)
        sis = np.linspace(si0, si1, rows)
        src = np.array([[lr, si] for si in sis for lr in lrs], float)
        sp_lr = (lr1 - lr0) / (cols - 1)
        sp_si = (si1 - si0) / (rows - 1)
        radius = api.WARP_GRID_RADIUS_FACTOR * 0.5 * (sp_lr + sp_si)
        cap = api.WARP_MAX_DISP_FRAC * min(sp_lr, sp_si)
        dst = src.copy()
        centre = (rows // 2) * cols + cols // 2
        dst[centre] += [cap * 0.25, cap * 0.15]
        verdict = api.validate_warp_grid(src, dst, radius, EXTENT, rows, cols)
        self.assertTrue(verdict["ok"], verdict["reason"])
        self.assertEqual(verdict["n_moved"], 1)
        self.assertEqual(api.warp_movable_count(rows, cols), 165)

    def test_oversized_grid_is_rejected_before_geometry_work(self):
        verdict = api.validate_warp_grid([], [], 1.0, EXTENT, 16, 16)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], "unsupported grid dimensions")


# ----------------------------------------------- numerical inverse of the forward
class TestWarpInverse(unittest.TestCase):
    """warp_csrbf_invert must invert the EXACT forward field the browser previews.

    The earlier implementation resampled with a second CSRBF fitted the other way
    round (centers = dst). That is only an approximate inverse: exact at the control
    points but drifting between them, up to p95 ~45 um / max ~49 um at the largest
    legal displacement -- about one 50 um atlas voxel, i.e. visible preview-vs-label
    disagreement. These tests pin the residual to the solver tolerance instead.
    """

    def setUp(self):
        self.src, self.R, self.cap = _lattice()
        # probe points: the nodes, off-node interior points and the extent corners
        lr0, lr1, si0, si1 = EXTENT
        rng = np.random.default_rng(7)
        interior = np.c_[rng.uniform(lr0, lr1, 600), rng.uniform(si0, si1, 600)]
        self.pts = np.vstack([self.src, interior,
                              [[lr0, si0], [lr1, si1], [lr0, si1], [lr1, si0]]])

    def _roundtrip(self, dst):
        """|F^-1(F(p)) - p| over the probe points."""
        fwd = api.warp_csrbf_points(self.src, dst, self.R, self.pts)
        back, stats = api.warp_csrbf_invert(self.src, dst, self.R, fwd)
        err = np.linalg.norm(back - self.pts, axis=1)
        return err, stats

    def test_identity_inverse_is_exact(self):
        """An identity grid must return the input unchanged, with no iteration."""
        back, stats = api.warp_csrbf_invert(self.src, self.src.copy(), self.R,
                                            self.pts)
        self.assertTrue(np.array_equal(back, self.pts))
        self.assertEqual(stats["iters"], 0)
        self.assertEqual(stats["n_bad"], 0)

    def test_single_small_move(self):
        err, stats = self._roundtrip(_moved(self.src, {CENTRE: (300.0, 200.0)}))
        self.assertEqual(stats["n_bad"], 0, "all points must converge")
        self.assertLess(float(np.percentile(err, 95)), 5.0)
        self.assertLess(float(err.max()), 10.0)

    def test_multi_node_combination(self):
        """Several interior nodes moved at once (opposed directions)."""
        u = self.cap * 0.5
        moves = {CENTRE: (u, u), CENTRE - 1: (-u, u),
                 CENTRE + 1: (u, -u), CENTRE - COLS: (-u, -u)}
        err, stats = self._roundtrip(_moved(self.src, moves))
        self.assertEqual(stats["n_bad"], 0)
        self.assertLess(float(np.percentile(err, 95)), 5.0)
        self.assertLess(float(err.max()), 10.0)

    def test_near_maximum_legal_displacement(self):
        """The case that used to be worst: a single node at ~the displacement cap."""
        dst = _moved(self.src, {CENTRE: (self.cap * 0.98, 0.0)})
        self.assertTrue(api.validate_warp_grid(self.src, dst, self.R, EXTENT)["ok"])
        err, stats = self._roundtrip(dst)
        self.assertEqual(stats["n_bad"], 0)
        self.assertLess(float(np.percentile(err, 95)), 5.0)
        self.assertLess(float(err.max()), 10.0)

    def test_all_nine_nodes_at_cap(self):
        """Worst legal grid we can build: every interior node pushed to the cap in
        an alternating pattern (min |J| ~0.12, still accepted by the guard)."""
        u = self.cap * 0.999 / np.sqrt(2.0)
        interior = [r * COLS + c for r in range(1, ROWS - 1) for c in range(1, COLS - 1)]
        dst = self.src.copy()
        for k, i in enumerate(interior):
            dst[i] = dst[i] + [u * (1 if k % 2 else -1), u * (1 if (k // 3) % 2 else -1)]
        if not api.validate_warp_grid(self.src, dst, self.R, EXTENT)["ok"]:
            self.skipTest("alternating-cap grid is not legal on this extent")
        err, stats = self._roundtrip(dst)
        self.assertEqual(stats["n_bad"], 0, f"stats={stats}")
        self.assertLess(float(np.percentile(err, 95)), 5.0)
        self.assertLess(float(err.max()), 10.0)

    def test_inverse_beats_the_old_pull_field(self):
        """Quantify the fix: the numerical inverse must be orders of magnitude more
        accurate than the single-shot pull field it replaced, on the same grid."""
        dst = _moved(self.src, {CENTRE: (self.cap * 0.98, 0.0)})
        fwd = api.warp_csrbf_points(self.src, dst, self.R, self.pts)
        # old approach: one CSRBF fitted dst -> src, evaluated once
        w_pull = api._csrbf_weights(dst, self.src - dst, self.R)
        old = fwd + api._csrbf_eval(dst, w_pull, self.R, fwd)
        old_err = np.linalg.norm(old - self.pts, axis=1)
        new_err, _ = self._roundtrip(dst)
        old_p95 = float(np.percentile(old_err, 95))
        new_p95 = float(np.percentile(new_err, 95))
        self.assertGreater(old_p95, 0.1,
                           "premise: the old pull field has measurable error")
        self.assertLess(new_p95, old_p95 / 100.0)

    def test_residual_reported_honestly_when_not_converged(self):
        """With max_iter=1 the solver cannot converge; it must SAY so rather than
        silently return a bad answer."""
        dst = _moved(self.src, {CENTRE: (self.cap * 0.98, 0.0)})
        fwd = api.warp_csrbf_points(self.src, dst, self.R, self.pts)
        back, stats = api.warp_csrbf_invert(self.src, dst, self.R, fwd, max_iter=1)
        self.assertGreater(stats["n_bad"], 0)
        self.assertGreater(stats["max_resid"], stats["tol"])
        self.assertTrue(np.all(np.isfinite(back)), "fallback must stay finite")

    def test_far_from_every_node_is_untouched(self):
        """Compact support: a point beyond R of every moved node is its own inverse."""
        dst = _moved(self.src, {CENTRE: (300.0, 200.0)})
        far = np.array([[EXTENT[0] - 5000.0, EXTENT[2] - 5000.0]])
        back, _ = api.warp_csrbf_invert(self.src, dst, self.R, far)
        self.assertLess(float(np.abs(back - far).max()), 1e-9)


# ------------------------------------------------- rejection must not brick a job
class TestRejectionLeavesJobUsable(unittest.TestCase):
    """An unacceptable grid is bad client input, not a pipeline failure.

    Regression test: the grid used to be validated only inside the finalize
    subprocess, which runs AFTER the job has been moved to FINALIZING. A rejected
    grid therefore raised from the worker, landed the job in FAILED and — because
    FAILED was not an accepted input state — made every later curation attempt fail
    with "job in state FAILED". The job was unrecoverable without re-registering.
    """

    def setUp(self):
        import tempfile
        import time
        from web import jobs
        self.jobs = jobs
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._orig = jobs.JOBS_DIR
        jobs.JOBS_DIR = os.path.join(self.tmp, "jobs")
        os.makedirs(jobs.JOBS_DIR, exist_ok=True)

        # a bundle carrying only what the pre-flight guards read: the AP and the
        # plane axes that give the displayed extent.
        lr0, lr1, si0, si1 = EXTENT
        self.bp = os.path.join(self.tmp, "manual_bundle_test.npz")
        np.savez(self.bp, ap=150, sampled_ap_idx=150, sampled_ap_um=150.0,
                 dx=float(config.PLANE_DX_UM),
                 xJ1=np.linspace(lr0, lr1, 64), xJ0=np.linspace(si0, si1, 64))
        self.job = {
            "id": "testjob", "state": "AUTO_REGISTRATION_DONE",
            "created": time.time(), "updated": time.time(),
            "exp_path": os.path.join(self.tmp, "exp"), "region_key": None,
            "name": "test", "tag": "_test", "out_dir": os.path.join(self.tmp, "out"),
            "orientation": {"angle": 0.0, "flip": "none"}, "ap_idx": 150,
            "ap_res_um": float(config.PLANE_DX_UM),
            "register": {"status": "done", "result": {"bundle_path": self.bp}},
            "manual": None, "error": None,
        }
        jobs.save(self.job)
        self.src, self.R, self.cap = _lattice()
        self.affine = {"cx": 0.0, "cy": 0.0, "tx": 0.0, "ty": 0.0,
                       "rot_deg": 0.0, "sx": 1.0, "sy": 1.0}

    def tearDown(self):
        self.jobs.JOBS_DIR = self._orig
        self._tmp.cleanup()

    def _warp(self, dst):
        return {"algo": api.WARP_GRID_ALGO, "rows": ROWS, "cols": COLS,
                "R": self.R, "src": self.src.tolist(), "dst": dst.tolist()}

    def test_bundle_extent_matches_warp_grid_extent(self):
        """The cheap extent reader agrees with the full-bundle version."""
        ext = self.jobs.bundle_extent(self.bp)
        for got, want in zip(ext, EXTENT):
            self.assertAlmostEqual(got, want, places=6)

    def test_corner_move_rejected_before_state_change(self):
        dst = _moved(self.src, {0: (60.0, 0.0)})
        with self.assertRaises(ValueError) as cm:
            self.jobs.confirm_curation("testjob", self.affine, self._warp(dst))
        self.assertIn("corner control point moved", str(cm.exception))
        after = self.jobs.load("testjob")
        self.assertEqual(after["state"], "AUTO_REGISTRATION_DONE",
                         "a rejected grid must leave the job state untouched")
        self.assertIsNone(after["manual"])

    def test_fold_rejected_before_state_change(self):
        i, j, ax = _fold_pair()
        step = np.zeros(2); step[ax] = self.cap * 0.999
        dst = self.src.copy()
        dst[i] = dst[i] + step
        dst[j] = dst[j] - step
        with self.assertRaises(ValueError) as cm:
            self.jobs.confirm_curation("testjob", self.affine, self._warp(dst))
        self.assertIn("folds", str(cm.exception))
        self.assertEqual(self.jobs.load("testjob")["state"], "AUTO_REGISTRATION_DONE")

    def test_failed_job_is_retryable(self):
        """A job left in FAILED can be curated again (its bundle is still good)."""
        j = self.jobs.load("testjob")
        j["state"] = "FAILED"
        j["error"] = "previous finalize died"
        self.jobs.save(j)
        # still rejected on its merits, but NOT because the state is FAILED
        dst = _moved(self.src, {0: (60.0, 0.0)})
        with self.assertRaises(ValueError) as cm:
            self.jobs.confirm_curation("testjob", self.affine, self._warp(dst))
        self.assertNotIn("job in state FAILED", str(cm.exception))
        self.assertIn("corner control point moved", str(cm.exception))

    def test_identity_grid_is_dropped_to_pure_affine(self):
        """An untouched grid never persists as a warp (kind stays global_affine)."""
        self.assertIsNone(self.jobs._clean_warp(self._warp(self.src.copy())))

    # ------------------------------------------------------------ disp_frac
    def test_disp_frac_absent_keeps_the_historical_cap(self):
        """The default path is untouched: no disp_frac -> WARP_MAX_DISP_FRAC.

        This is the whole safety argument for the feature -- every grid authored
        by the affine, direct-drag and node tools omits the field.
        """
        cleaned = self.jobs._clean_warp(
            self._warp(_moved(self.src, {CENTRE: (self.cap * 0.5, 0.0)})))
        self.assertNotIn("disp_frac", cleaned)
        ext = self.jobs.bundle_extent(self.bp)
        self.assertAlmostEqual(api.warp_max_disp(ext, ROWS, COLS, None),
                               self.cap, places=6)

    def _coherent_push(self, delta, sigma_frac=0.45):
        """A smooth, coherent displacement of the whole lattice -- the shape an
        elastic push actually produces, as opposed to yanking one node.

        The distinction matters: the raised budget exists to move a NEIGHBOURHOOD
        together. A lone node driven past the default cap is a spike, and the fold
        floor is supposed to reject it however large the budget is.
        """
        lr0, lr1, si0, si1 = EXTENT
        sigma = sigma_frac * min(lr1 - lr0, si1 - si0)
        kinds = api.warp_node_kinds(ROWS, COLS)
        dst = self.src.copy()
        for i, (lr, si) in enumerate(self.src):
            if kinds[i] == api.WARP_NODE_CORNER:
                continue
            w = np.exp(-0.5 * ((lr / sigma) ** 2 + (si / sigma) ** 2))
            step = np.array(delta, float) * w
            if kinds[i] == api.WARP_NODE_EDGE_V:
                step[0] = 0.0
            elif kinds[i] == api.WARP_NODE_EDGE_H:
                step[1] = 0.0
            dst[i] = dst[i] + step
        return dst

    def test_over_cap_grid_is_rejected_by_default_but_accepted_with_disp_frac(self):
        """One grid, two verdicts -- the budget is the only thing that changed."""
        dst = self._coherent_push([0.0, self.cap * 1.5])
        disp = np.linalg.norm(dst - self.src, axis=1).max()
        self.assertGreater(disp, self.cap, "the push must exceed the default cap")

        with self.assertRaises(ValueError) as cm:
            self.jobs.confirm_curation("testjob", self.affine, self._warp(dst))
        self.assertIn("displacement exceeds limit", str(cm.exception))
        self.assertEqual(self.jobs.load("testjob")["state"],
                         "AUTO_REGISTRATION_DONE")

        warp = self._warp(dst)
        warp["disp_frac"] = api.WARP_MAX_DISP_FRAC_CEILING
        cleaned = self.jobs._clean_warp(warp)
        self.assertAlmostEqual(cleaned["disp_frac"],
                               api.WARP_MAX_DISP_FRAC_CEILING, places=9)
        self.jobs._assert_warp_valid(cleaned, self.bp)   # must not raise

    def test_raised_budget_does_not_license_a_single_node_spike(self):
        """The budget buys COHERENT range, not the right to fold the map.

        A lone node pulled past the default cap stays rejected even at the ceiling
        -- now by the stricter fold floor rather than by the cap. Without this the
        raised budget would be a way to smuggle a spike past the validator.
        """
        dst = _moved(self.src, {CENTRE: (self.cap * 1.6, 0.0)})
        warp = self._warp(dst)
        warp["disp_frac"] = api.WARP_MAX_DISP_FRAC_CEILING
        with self.assertRaises(ValueError) as cm:
            self.jobs._assert_warp_valid(self.jobs._clean_warp(warp), self.bp)
        self.assertIn("folds or over-stretches", str(cm.exception))

    def test_disp_frac_is_clamped_to_the_ceiling(self):
        """A client asking for an unbounded budget gets the ceiling, not its wish."""
        warp = self._warp(_moved(self.src, {CENTRE: (self.cap * 0.5, 0.0)}))
        warp["disp_frac"] = 50.0
        self.assertAlmostEqual(self.jobs._clean_warp(warp)["disp_frac"],
                               api.WARP_MAX_DISP_FRAC_CEILING, places=9)

        ext = self.jobs.bundle_extent(self.bp)
        self.assertAlmostEqual(api.warp_max_disp(ext, ROWS, COLS, 50.0),
                               api.warp_max_disp(
                                   ext, ROWS, COLS,
                                   api.WARP_MAX_DISP_FRAC_CEILING),
                               places=6)
        # a push beyond even the ceiling is still refused. Which gate catches it
        # is deliberately not asserted: past the ceiling the displacement cap and
        # the fold floor both apply, and pinning one makes the test brittle
        # against exactly the kind of recalibration that produced it.
        dst = self._coherent_push([0.0, self.cap * 8.0])
        big = self._warp(dst)
        big["disp_frac"] = 50.0
        with self.assertRaises(ValueError) as cm:
            self.jobs.confirm_curation("testjob", self.affine,
                                       self.jobs._clean_warp(big))
        self.assertIn("rejected", str(cm.exception))
        self.assertEqual(self.jobs.load("testjob")["state"],
                         "AUTO_REGISTRATION_DONE")

    def test_node_crossing_is_caught_at_the_raised_cap(self):
        """The gate that only becomes load-bearing above 0.5 x spacing.

        At 0.45 two neighbours cannot close their gap; at 0.9 they can, so the
        node-ordering check -- not the displacement cap -- is what now stops the
        lattice inverting.
        """
        i, j, ax = _fold_pair()
        ext = self.jobs.bundle_extent(self.bp)
        cap9 = api.warp_max_disp(ext, ROWS, COLS,
                                 api.WARP_MAX_DISP_FRAC_CEILING)
        sp = min((ext[1] - ext[0]) / (COLS - 1), (ext[3] - ext[2]) / (ROWS - 1))
        self.assertGreater(2 * cap9, sp, "crossing must be reachable at 0.9")

        step = np.zeros(2); step[ax] = cap9 * 0.999
        dst = self.src.copy()
        dst[i] = dst[i] + step
        dst[j] = dst[j] - step
        warp = self._warp(dst)
        warp["disp_frac"] = api.WARP_MAX_DISP_FRAC_CEILING
        with self.assertRaises(ValueError) as cm:
            self.jobs.confirm_curation("testjob", self.affine,
                                       self.jobs._clean_warp(warp))
        self.assertIn("crossed a neighbour", str(cm.exception))
        self.assertEqual(self.jobs.load("testjob")["state"],
                         "AUTO_REGISTRATION_DONE")

    def test_malformed_disp_frac_is_rejected(self):
        warp = self._warp(_moved(self.src, {CENTRE: (self.cap * 0.5, 0.0)}))
        warp["disp_frac"] = "wide"
        with self.assertRaises(ValueError) as cm:
            self.jobs._clean_warp(warp)
        self.assertIn("disp_frac", str(cm.exception))
        # a non-positive request falls back to the default rather than raising
        warp["disp_frac"] = 0.0
        self.assertNotIn("disp_frac", self.jobs._clean_warp(warp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
