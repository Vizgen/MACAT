"""allen_alignment.raster: the registration's numpy core (2026-10-27).

The default registration used to call STalign's rasterize/normalize and torch's
grid_sample (via STalign.interp3D). They were replaced by numpy, and the results
were shown bit-identical on all 12 positive controls and two real experiments
before STalign and torch were dropped from the codebase
(2026-10-27). These tests pin the conventions, so existing jobs' labels cannot
move.
"""
import os
import sys
import unittest

import numpy as np

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from allen_alignment import raster              # noqa: E402

def _cells(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    return rng.uniform(0, 4000, n), rng.uniform(0, 2500, n)


class DensityRasterTests(unittest.TestCase):
    def test_every_cell_contributes_one(self):
        x, y = _cells()
        _, _, M = raster.density_raster(x, y, 25.0)
        self.assertAlmostEqual(M.sum(), len(x), places=6)

    def test_grid_covers_the_cells_widened_by_expand(self):
        x, y = _cells()
        xs, ys, M = raster.density_raster(x, y, 25.0)
        self.assertEqual(M.shape, (len(ys), len(xs)))
        self.assertTrue(np.allclose(np.diff(xs), 25.0) and np.allclose(np.diff(ys), 25.0))
        self.assertLess(xs[0], x.min()); self.assertGreater(xs[-1], x.max() - 25.0)

    def test_a_lone_cell_is_a_centred_symmetric_blob(self):
        x = np.array([0.0, 1000.0, 500.0]); y = np.array([0.0, 1000.0, 500.0])
        xs, ys, M = raster.density_raster(x, y, 25.0)
        r = int(np.argmin(np.abs(ys - 500.0))); c = int(np.argmin(np.abs(xs - 500.0)))
        self.assertEqual(M[r - 6:r + 7, c - 6:c + 7].argmax(), 6 * 13 + 6, "peak at its pixel")
        self.assertAlmostEqual(M[r, c - 2], M[r, c + 2], places=12)
        self.assertAlmostEqual(M[r - 2, c], M[r + 2, c], places=12)

    def test_chunking_does_not_change_the_result(self):
        x, y = _cells(5000)
        old = raster._CHUNK
        try:
            raster._CHUNK = 700
            a = raster.density_raster(x, y, 25.0)[2]
        finally:
            raster._CHUNK = old
        b = raster.density_raster(x, y, 25.0)[2]
        self.assertTrue(np.array_equal(a, b))


class SampleNearestTests(unittest.TestCase):
    def setUp(self):
        self.vol = np.arange(4 * 5 * 6, dtype=np.int32).reshape(4, 5, 6) + 1
        self.axes = [np.arange(4) * 10.0, np.arange(5) * 10.0 + 100, np.arange(6) * 10.0 - 50]

    def test_voxel_centres_return_their_voxel(self):
        pts = np.array([[20.0, 130.0, -10.0]])
        self.assertEqual(raster.sample_nearest(self.axes, self.vol, pts)[0], self.vol[2, 3, 4])

    def test_nearest_rounds_and_outside_is_zero(self):
        pts = np.array([[14.0, 100.0, -50.0],    # 1.4 -> voxel 1
                        [-6.0, 100.0, -50.0],    # -0.6 -> -1: outside
                        [-4.0, 100.0, -50.0]])   # -0.4 -> 0: inside
        got = raster.sample_nearest(self.axes, self.vol, pts)
        self.assertEqual(list(got), [self.vol[1, 0, 0], 0, self.vol[0, 0, 0]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
