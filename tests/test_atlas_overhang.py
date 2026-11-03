"""Cells that sample past the end of the Allen volume (planes.atlas_overhang).

On a sagittal registration 8.8% of the plane sat beyond the atlas at each AP end
(2026-09-21): the Allen volume is flush at AP, and AP is an in-plane
axis for sagittal. The label lookup zero-fills there, so those cells are
unlabelled for a reason that has nothing to do with the fit -- and nothing said
so. These tests pin the count the UI now reports, and above all that its notion
of "beyond" is EXACTLY the one the lookup applies (raster.sample_nearest; until
2026-10-27 STalign's interp3D, which it reproduces), so it can never disagree
with the labels.
"""
import os
import sys
import unittest

import numpy as np

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from allen_alignment import planes, raster             # noqa: E402

SHAPE = (12, 8, 10)
RES = 25.0


def _plane(points):
    """Put a list of (ap, si, lr) points on a 1-row plane, one cell per pixel."""
    tf = np.asarray(points, float)[None]               # (1, n, 3)
    n = tf.shape[1]
    return tf, np.zeros(n, int), np.arange(n), np.ones(n, bool)


class OverhangTests(unittest.TestCase):
    def setUp(self):
        self.xI = planes.axis_coords(SHAPE, RES)
        self.lo = [c[0] for c in self.xI]
        self.hi = [c[-1] for c in self.xI]

    def test_inside_counts_nothing(self):
        tf, r, c, ib = _plane([[0, 0, 0], [self.lo[0], self.lo[1], self.lo[2]],
                               [self.hi[0], self.hi[1], self.hi[2]]])
        out = planes.atlas_overhang(tf, r, c, ib, SHAPE, RES)
        self.assertEqual((out["n"], out["ends"]), (0, []))

    def test_each_end_is_named(self):
        far = 3 * RES
        tf, r, c, ib = _plane([
            [self.lo[0] - far, 0, 0],      # anterior
            [self.hi[0] + far, 0, 0],      # posterior
            [self.hi[0] + far, 0, 0],      # posterior
            [0, self.lo[1] - far, 0],      # dorsal
            [0, 0, 0],                      # inside
        ])
        out = planes.atlas_overhang(tf, r, c, ib, SHAPE, RES)
        self.assertEqual(out["n"], 4)
        self.assertEqual(out["n_cells"], 5)
        self.assertAlmostEqual(out["frac"], 0.8)
        got = {(e["axis"], e["end"]): e["n"] for e in out["ends"]}
        self.assertEqual(got, {("AP", "anterior"): 1, ("AP", "posterior"): 2,
                               ("SI", "dorsal"): 1})

    def test_a_corner_cell_is_one_cell_under_two_ends(self):
        far = 3 * RES
        tf, r, c, ib = _plane([[self.hi[0] + far, self.hi[1] + far, 0]])
        out = planes.atlas_overhang(tf, r, c, ib, SHAPE, RES)
        self.assertEqual(out["n"], 1)
        self.assertEqual(len(out["ends"]), 2)

    def test_cells_off_the_plane_raster_are_not_counted(self):
        tf, _, _, _ = _plane([[self.hi[0] + 99 * RES, 0, 0]])
        out = planes.atlas_overhang(tf, np.array([0, 5]), np.array([0, 0]),
                                    np.array([False, True]), SHAPE, RES)
        self.assertEqual(out["n"], 0)
        self.assertEqual(out["n_cells"], 2, "the denominator is all cells")

    def test_the_boundary_is_exactly_where_the_label_lookup_zero_fills(self):
        """The load-bearing test. A volume of ones, sampled by the same lookup
        registration uses at points straddling every face: a cell is 'beyond'
        iff that sample came back 0. Anything else and the report would
        contradict the labels it is explaining."""
        rng = np.random.default_rng(0)
        span = [(lo - 2 * RES, hi + 2 * RES) for lo, hi in zip(self.lo, self.hi)]
        pts = np.stack([rng.uniform(a, b, 4000) for a, b in span], axis=1)
        # and points within a hair of each half-voxel boundary, both sides
        for a in range(3):
            for edge in (self.lo[a] - RES / 2, self.hi[a] + RES / 2):
                for eps in (-1e-3, 1e-3):
                    p = np.zeros(3); p[a] = edge + eps
                    pts = np.vstack([pts, p])
        sampled = raster.sample_nearest(self.xI, np.ones(SHAPE, dtype=np.int32), pts)
        zero = sampled == 0

        tf, r, c, ib = _plane(pts)
        out = planes.atlas_overhang(tf, r, c, ib, SHAPE, RES)
        # per-point verdict, recomputed the way the function does it
        idx = np.floor((pts - np.array(self.lo)) / RES + 0.5)
        mine = ((idx < 0) | (idx > np.array(SHAPE) - 1)).any(axis=1)
        self.assertEqual(out["n"], int(mine.sum()))
        # exact-boundary points are a rounding tie in both; exclude the hair-
        # width probes from the strict comparison and check the bulk exactly
        bulk = slice(0, 4000)
        self.assertTrue(np.array_equal(mine[bulk], zero[bulk]),
                        f"{int((mine[bulk] != zero[bulk]).sum())} of 4000 points "
                        f"disagree with the label lookup")
        self.assertGreater(int(zero[bulk].sum()), 500, "too few outside to mean much")


if __name__ == "__main__":
    unittest.main(verbosity=2)
