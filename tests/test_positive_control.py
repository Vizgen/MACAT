"""The positive-control manifest (data/positive_control/sections.csv).

It is the answer key for alignment tests -- which coronal or sagittal level and
which orientation are correct for each Zhuang-lab section -- so it must stay
self-consistent: levels that follow from the position it records, at both
resolutions, inside the range the step-3 slider offers for that plane. The
bulky section data is gitignored; these tests need only the tracked manifest.
"""
import csv
import math
import os
import sys
import unittest

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from allen_alignment import planes                       # noqa: E402

_MANIFEST = os.path.join(_STUDY, "data", "positive_control", "sections.csv")
_SHAPES = {25: (528, 320, 456), 50: (264, 160, 228)}


def _manifest():
    with open(_MANIFEST, newline="") as fh:
        return list(csv.DictReader(fh))


@unittest.skipUnless(os.path.exists(_MANIFEST), "positive-control manifest not built")
class ManifestTests(unittest.TestCase):
    def test_the_three_sets_are_all_there(self):
        """5 whole coronal (Allen), 5 coronal hemisections (Zhuang), 2 sagittal
        (Zhuang) -- decided 2026-10-20."""
        kinds = [(r["plane"], r["section_type"], r["dataset"]) for r in _manifest()]
        self.assertEqual(kinds.count(("coronal", "whole", "MERFISH-C57BL6J-638850")), 5)
        self.assertEqual(kinds.count(("coronal", "hemisection", "Zhuang-ABCA-1")), 5)
        self.assertEqual(kinds.count(("sagittal", "whole", "Zhuang-ABCA-3")), 2)
        for r in _manifest():
            self.assertTrue(r["name"].startswith(f"pc_{r['plane']}_"))
            self.assertEqual(r["section_type"] == "hemisection", "_hemi_" in r["name"])

    def test_every_hemisection_declares_its_half(self):
        """The scorer declares it in step 2 as a user would; a whole section
        declares none. Which half follows from the ground truth's ML side."""
        for r in _manifest():
            want = ("left", "right") if r["section_type"] == "hemisection" else ("",)
            self.assertIn(r.get("hemisection", ""), want, r["name"])

    def test_sections_from_the_qc_reference_are_held_out_of_it(self):
        """The Allen sections are from the dataset the QC reference is built
        from; scored against it unmodified, a section would partly match
        itself. They must be flagged, and excluded from reference_holdout/."""
        for r in _manifest():
            from_reference = r["dataset"] == "MERFISH-C57BL6J-638850"
            self.assertEqual(r["held_out_of_reference"] == "True", from_reference, r["name"])

    def test_levels_follow_from_the_recorded_position(self):
        """index = microns along the slice axis / resolution, from the atlas
        corner -- the convention section_corr and the provenance doc use."""
        for r in _manifest():
            um = float(r["slice_mm"]) * 1000
            for res in (25, 50):
                self.assertLessEqual(abs(int(r[f"idx_{res}um"]) - um / res), 0.6,
                                     f"{r['name']} @{res}um")
                self.assertGreater(int(r[f"idx_{res}um_tolerance"]), 0)
                if r["plane"] == "sagittal":
                    n = _SHAPES[res][2]
                    self.assertEqual(int(r[f"idx_{res}um_mirror"]),
                                     n - 1 - int(r[f"idx_{res}um"]))

    def test_every_level_is_selectable_in_step_3(self):
        for r in _manifest():
            for res, shape in _SHAPES.items():
                s = planes.slider(shape, float(res), r["plane"])
                idx = int(r[f"idx_{res}um"])
                self.assertTrue(s["lo"] <= idx <= s["hi"],
                                f"{r['name']}: level {idx} outside the {res} um "
                                f"{r['plane']} slider {s['lo']}..{s['hi']}")

    def test_orientations_are_ones_step_2_offers(self):
        for r in _manifest():
            a = float(r["orientation_angle"])
            self.assertTrue(0 <= a < 360 and math.isclose(a, round(a)))
            self.assertIn(r["orientation_flip"], ("none", "x", "y"))
            self.assertGreater(float(r["orientation_score"]), 1.9,
                               f"{r['name']}: orientation fit is not clear-cut")


class DiscoveryTests(unittest.TestCase):
    def test_built_controls_are_offered_in_step_1(self):
        import config
        for name, path in config.POSITIVE_CONTROLS.items():
            self.assertEqual(config.REGIONS[name], path)
            self.assertIn(config.REGION_PLANE[name], ("coronal", "sagittal"))
            self.assertTrue(os.path.exists(os.path.join(path, "cell_metadata.csv")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
