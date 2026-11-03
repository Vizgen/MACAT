"""Coronal hemisections: half a brain, declared in step 2.

The whole-section fit matches the tissue's span to the WHOLE atlas slice and
puts its centre on the midline, so half a brain was stretched across both
hemispheres: 3-21% region agreement on the positive-control hemisections
(2026-10-20). A hemisection is now declared ("left"/"right" half as displayed)
and fitted against that half only.
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from allen_alignment import planes                     # noqa: E402
from web import jobs                                   # noqa: E402


def _ellipse(h, w, cy, cx, ry, rx):
    yy, xx = np.mgrid[:h, :w]
    return ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1


class FitTests(unittest.TestCase):
    """A synthetic slice: a 100 x 200 px brain (two 100-wide hemispheres) on a
    25 um grid whose centred coordinates put the midline at 0."""

    RES = 25.0

    def setUp(self):
        n = 260
        self.cols = np.arange(n) * self.RES - (n - 1) * self.RES / 2   # midline ~0
        self.atlas = _ellipse(160, n, 80, (n - 1) / 2, 50, 100)
        # the tissue: the left half alone, on its own raster
        self.tissue = np.zeros((140, 140), bool)
        self.tissue[20:121, 20:121] = _ellipse(140, 140, 70, 120, 50, 100)[20:121, 20:121]

    def test_left_half_goes_left_at_half_width(self):
        r, c, centre = planes.hemisection_fit(self.atlas, self.tissue, self.cols, "left")
        self.assertLess(centre, 0, "the left half's centre is left of the midline")
        # the atlas half is ~100 px wide, like the tissue: no stretch
        self.assertAlmostEqual(c, 1.0, delta=0.05)
        self.assertAlmostEqual(r, 1.0, delta=0.05)
        self.assertAlmostEqual(centre, -50 * self.RES, delta=3 * self.RES)

    def test_right_half_mirrors_it(self):
        _, _, left = planes.hemisection_fit(self.atlas, self.tissue, self.cols, "left")
        _, _, right = planes.hemisection_fit(self.atlas, self.tissue, self.cols, "right")
        self.assertAlmostEqual(left, -right, delta=2 * self.RES)

    def test_the_whole_slice_fit_would_have_stretched_it_two_fold(self):
        """What the whole-section fit did to half a brain, for contrast."""
        def span(m, axis):
            idx = np.where(m.any(axis=1 - axis))[0]
            return idx[-1] - idx[0] + 1
        self.assertGreater(span(self.atlas, 1) / span(self.tissue, 1), 1.9)

    def test_an_empty_half_is_refused(self):
        atlas = self.atlas.copy()
        atlas[:, self.cols < 0] = False
        with self.assertRaisesRegex(ValueError, "no tissue in its left half"):
            planes.hemisection_fit(atlas, self.tissue, self.cols, "left")


class NormalizeTests(unittest.TestCase):
    def test_whole_means_none(self):
        for v in (None, "", "whole", "none"):
            self.assertIsNone(planes.normalize_hemisection(v, "coronal"))

    def test_sides(self):
        self.assertEqual(planes.normalize_hemisection("Left", "coronal"), "left")
        self.assertEqual(planes.normalize_hemisection("right", None), "right")

    def test_sagittal_and_nonsense_are_refused(self):
        with self.assertRaisesRegex(ValueError, "coronal sections only"):
            planes.normalize_hemisection("left", "sagittal")
        with self.assertRaisesRegex(ValueError, "unknown hemisection"):
            planes.normalize_hemisection("top", "coronal")


class JobStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(jobs, "JOBS_DIR", self.tmp.name)
        p.start(); self.addCleanup(p.stop)

    def _job(self, orientation, state="AUTO_REGISTRATION_DONE"):
        j = {"id": "h", "state": state, "created": time.time(), "updated": time.time(),
             "orientation": orientation, "ap_idx": 300, "ap_res_um": 25.0,
             "register": {"status": "done", "result": {"x": 1}},
             "manual": None, "error": None}
        os.makedirs(jobs.job_dir("h"))
        return jobs.save(j)

    def test_it_is_stored_with_the_orientation(self):
        self._job({"angle": 0.0, "flip": "none", "plane": "coronal"}, "INPUT_READY")
        j = jobs.set_orientation("h", 0, "none", "coronal", "left")
        self.assertEqual(j["orientation"]["hemisection"], "left")

    def test_an_older_job_reads_as_whole_so_reconfirming_changes_nothing(self):
        """Every job before 2026-10-20 has no field; it must not look changed,
        or re-confirming an unchanged orientation would discard a registration."""
        self._job({"angle": 0.0, "flip": "none", "plane": "coronal"})
        j = jobs.set_orientation("h", 0, "none", "coronal", None)
        self.assertEqual(j["state"], "AUTO_REGISTRATION_DONE")
        self.assertEqual(j["register"]["status"], "done")

    def test_declaring_it_invalidates_the_registration(self):
        self._job({"angle": 0.0, "flip": "none", "plane": "coronal", "hemisection": None})
        j = jobs.set_orientation("h", 0, "none", "coronal", "right")
        self.assertEqual(j["state"], "ORIENTATION_CONFIRMED")
        self.assertEqual(j["register"], {"status": "idle"})

    def test_sagittal_refuses_it(self):
        self._job({"angle": 0.0, "flip": "none", "plane": "sagittal"}, "INPUT_READY")
        with self.assertRaises(ValueError):
            jobs.set_orientation("h", 0, "none", "sagittal", "left")


if __name__ == "__main__":
    unittest.main(verbosity=2)
