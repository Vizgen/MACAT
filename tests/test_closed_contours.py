"""plane.closed_contours: outlines that never close with a chord.

2026-10-21, reported on a hemisection: the Landmarks outline drew a straight
diagonal across the cortex, and the same line showed in Results. The tissue
touched the edge of the plane raster on its midline side, find_contours returned
an OPEN contour there, and closing it (last point back to first) drew a chord.
The fix pads before tracing, so a region on the edge closes ALONG the edge.
Six call sites use it: the three outline tracers, the curation polygons, the
GeoJSON deliverable and the region-QC polygons.
"""
import os
import sys
import unittest

import numpy as np
from skimage import measure

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from allen_alignment.plane import closed_contours        # noqa: E402


def _max_step(c):
    return float(np.hypot(*np.diff(c, axis=0).T).max())


class ClosedContourTests(unittest.TestCase):
    def test_a_region_on_the_edge_closes_along_it_not_across_it(self):
        mask = np.zeros((60, 60), bool)
        yy, xx = np.mgrid[:60, :60]
        mask[(yy - 30) ** 2 + (xx - 60) ** 2 <= 28 ** 2] = True     # a half-disc cut by the edge
        old = max(measure.find_contours(mask.astype(float), 0.5), key=len)
        self.assertGreater(np.hypot(*(old[0] - old[-1])), 40,
                           "precondition: the unpadded contour is open across the cut")
        new = max(closed_contours(mask), key=len)
        self.assertTrue(np.allclose(new[0], new[-1]), "the contour must be closed")
        self.assertLessEqual(_max_step(new), 1.5, "no long chord anywhere in the ring")
        self.assertAlmostEqual(new[:, 1].max(), 59.5, places=6,
                               msg="it runs along the raster edge, half a pixel out")

    def test_a_region_off_the_edge_is_traced_exactly_as_before(self):
        """So whole sections, which rarely touch the edge, do not change."""
        mask = np.zeros((50, 50), bool)
        mask[10:30, 12:40] = True
        a = measure.find_contours(mask.astype(float), 0.5)
        b = closed_contours(mask)
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            self.assertTrue(np.allclose(x, y))

    def test_coordinates_stay_in_the_unpadded_frame(self):
        mask = np.zeros((20, 20), bool)
        mask[0:5, 0:5] = True                         # the corner
        c = max(closed_contours(mask), key=len)
        self.assertAlmostEqual(c[:, 0].min(), -0.5)
        self.assertAlmostEqual(c[:, 1].min(), -0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
