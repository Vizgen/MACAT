"""What happens to the step-3 slider and to existing jobs when the atlas
resolution changes.

Two things were open before 25 um could be switched on :

  * the slider's bounds were constants written for the 264-slice 50 um atlas,
    which address only the front half of the 528-slice 25 um one;
  * every bundle on disk is 50 um, and an index means a different slice at each
    resolution (150 is 925 um at 50 um; 925 um is between 300 and 301 at 25 um).

Decided 2026-10-07: a job from another resolution is REFUSED, not migrated --
nothing about it changes until someone re-picks its slice, which carries over by
microns. These tests pin that, at 50 um and with the server pretending to run
at 25 um. The 25 um side needs only the atlas SHAPE, so it is patched in rather
than downloaded.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import config                                        # noqa: E402
from allen_alignment import atlas, planes             # noqa: E402
from web import jobs                                  # noqa: E402

SHAPE_50 = (264, 160, 228)
SHAPE_25 = (528, 320, 456)


def at_50um():
    """Make this process behave like a server started at 50 um."""
    return [mock.patch.object(config, "PLANE_DX_UM", 50.0),
            mock.patch.object(jobs, "_here_shape", lambda: SHAPE_50)]


def at_25um():
    """Make this process behave like a server started at 25 um."""
    return [mock.patch.object(config, "PLANE_DX_UM", 25.0),
            mock.patch.object(jobs, "_here_shape", lambda: SHAPE_25)]


class SliderTests(unittest.TestCase):
    def test_fifty_um_reproduces_the_historical_slider(self):
        """At 50 um nothing a user sees may move: 40..230 starting at 150 on
        coronal, and the same fractions and start on sagittal."""
        c = planes.slider(SHAPE_50, 50.0, "coronal")
        self.assertEqual((c["lo"], c["hi"], c["default_idx"]), (40, 230, 150))
        s = planes.slider(SHAPE_50, 50.0, "sagittal")
        self.assertEqual((s["lo"], s["hi"], s["default_idx"]), (34, 198, 150))

    def test_twenty_five_um_covers_the_whole_brain(self):
        """The old constants reached only to index 230 of 528."""
        c = planes.slider(SHAPE_25, 25.0, "coronal")
        self.assertEqual((c["lo"], c["hi"]), (79, 459))
        # 925 um is equidistant from 300 and 301; the first wins, consistently
        self.assertEqual(c["default_idx"], 300)

    def test_the_micron_scale_matches_the_registration_grid(self):
        """origin + idx * step must be the same number registration uses."""
        for shape, res in ((SHAPE_50, 50.0), (SHAPE_25, 25.0)):
            xI = planes.axis_coords(shape, res)
            for p in planes.PLANES:
                m = planes.slider(shape, res, p)
                i = m["default_idx"]
                self.assertAlmostEqual(m["origin_um"] + i * m["step_um"],
                                       planes.slice_coordinate(xI, i, p))

    def test_axis_coords_is_the_formula_registration_had_inline(self):
        res = 50.0
        got = planes.axis_coords(SHAPE_50, res)
        for n, g in zip(SHAPE_50, got):
            self.assertTrue(np.array_equal(
                g, np.arange(n) * res - (n - 1) * res / 2))


class BundleGuardTests(unittest.TestCase):
    def test_reads_dx_and_treats_a_bundle_without_it_as_fifty(self):
        self.assertEqual(atlas.bundle_res_um({"dx": np.array(25.0)}), 25.0)
        # deliberately NOT read from the xJ0 spacing (110 um here): synthetic
        # fixtures in test_warp_grid have arbitrary grids and no dx
        self.assertEqual(
            atlas.bundle_res_um({"xJ0": np.linspace(0, 7000, 64)}), 50.0)

    def test_matching_bundle_passes(self):
        here = float(config.PLANE_DX_UM)
        self.assertEqual(atlas.check_bundle_resolution({"dx": np.array(here)}), here)

    def test_a_fifty_um_bundle_is_refused_at_twenty_five(self):
        with mock.patch.object(config, "PLANE_DX_UM", 25.0):
            with self.assertRaisesRegex(atlas.ResolutionMismatch, "re-run registration"):
                atlas.check_bundle_resolution({"dx": np.array(50.0)})

    def test_refusal_is_a_value_error(self):
        """Existing `except ValueError` paths (the report archive) still skip it."""
        self.assertTrue(issubclass(atlas.ResolutionMismatch, ValueError))


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(jobs, "JOBS_DIR", self.tmp.name)
        p.start(); self.addCleanup(p.stop)

    def _bundle(self, dx):
        path = os.path.join(self.tmp.name, f"bundle_{dx:g}.npz")
        np.savez(path, dx=np.array(dx), ap=np.array(150),
                 sampled_ap_idx=np.array(150), sampled_ap_um=np.array(925.0))
        return path

    def _legacy_completed(self, plane="coronal"):
        """A job exactly as every one on disk was written: no ap_res_um/ap_um."""
        job = {"id": "legacy", "state": "COMPLETED", "ap_idx": 150,
               "orientation": {"angle": 0.0, "flip": "none", "plane": plane},
               "register": {"status": "done",
                            "result": {"bundle_path": self._bundle(50.0)}},
               "manual": {"status": "done"}, "error": None}
        os.makedirs(jobs.job_dir("legacy"))
        return jobs.save(job)

    def _run_at(self, patches):
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def _run_at_25(self):
        self._run_at(at_25um())

    def _run_at_50(self):
        self._run_at(at_50um())

    def test_legacy_job_is_fine_at_fifty(self):
        self._run_at_50()
        chk = jobs.atlas_check(self._legacy_completed())
        self.assertTrue(chk["ok"], chk)
        self.assertEqual(chk["ap_idx_here"], 150)

    def test_legacy_job_is_flagged_at_twenty_five_and_carried_by_microns(self):
        job = self._legacy_completed()
        self._run_at_25()
        chk = jobs.atlas_check(job)
        self.assertFalse(chk["ok"])
        self.assertIn("50 um atlas", chk["reason"])
        self.assertEqual(chk["bundle_res_um"], 50.0)
        self.assertEqual(chk["ap_idx_here"], 300)          # 925 um, not index 150
        self.assertEqual(jobs.load("legacy")["state"], "COMPLETED",
                         "reading the check must not change the job")

    def test_sagittal_carries_on_its_own_axis(self):
        job = self._legacy_completed(plane="sagittal")
        self._run_at_25()
        # 1825 um on LR; at 25 um that is 1825 + 5687.5 = 7512.5 -> 300.5 -> 300
        self.assertEqual(jobs.atlas_check(job)["ap_idx_here"], 300)

    def test_curation_is_refused(self):
        job = self._legacy_completed()
        self._run_at_25()
        with self.assertRaises(atlas.ResolutionMismatch):
            jobs.assert_resolution_consistent(job)

    def test_registration_with_an_old_index_is_refused(self):
        job = self._legacy_completed()
        job["state"] = "AP_CONFIRMED"; jobs.save(job)
        self._run_at_25()
        with self.assertRaisesRegex(ValueError, "re-confirm it in step 3"):
            jobs.start_register("legacy")

    def test_repicking_records_resolution_and_microns(self):
        """Even the SAME index is a change across resolutions: index 300 at 25 um
        is not what index 300 meant before. The job rolls back to AP_CONFIRMED."""
        self._legacy_completed()
        self._run_at_25()
        j = jobs.set_ap("legacy", 300)
        self.assertEqual(j["state"], "AP_CONFIRMED")
        self.assertEqual(j["ap_res_um"], 25.0)
        self.assertAlmostEqual(j["ap_um"], 912.5)
        self.assertIsNone(j["manual"])
        self.assertEqual(j["register"], {"status": "idle"})

    def test_unchanged_repick_at_the_same_resolution_is_a_noop(self):
        self._legacy_completed()
        self._run_at_50()
        j = jobs.set_ap("legacy", 150)
        self.assertEqual(j["state"], "COMPLETED")
        self.assertEqual((j["ap_res_um"], j["ap_um"]), (50.0, 925.0))

    def test_out_of_range_index_is_refused(self):
        self._legacy_completed()
        self._run_at_50()
        with self.assertRaisesRegex(ValueError, "outside the 50 um atlas"):
            jobs.set_ap("legacy", 264)


if __name__ == "__main__":
    unittest.main(verbosity=2)
