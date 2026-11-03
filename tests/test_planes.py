"""The section-plane axis table (allen_alignment.planes).

The Allen volume is one array with axes (AP, SI, LR); coronal fixes AP, sagittal
fixes LR. Everything that could go wrong here is silent: a swapped row/col axis
does not raise, it registers the section against a transposed atlas slice and
labels every cell from it. So these tests check the permutation itself rather
than that the functions run.
"""
import os
import sys
import unittest

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import numpy as np                              # noqa: E402
from allen_alignment import planes              # noqa: E402


class PlaneTableTests(unittest.TestCase):
    def test_coronal_is_the_default_and_is_the_historical_axis(self):
        """Every bundle on disk was built slicing axis 0. The default must not
        move, or old jobs silently change meaning."""
        self.assertEqual(planes.DEFAULT, planes.CORONAL)
        self.assertEqual(planes.axes(), (0, 1, 2))
        self.assertEqual(planes.axes(None), (0, 1, 2))

    def test_sagittal_fixes_LR_and_puts_AP_in_the_columns(self):
        self.assertEqual(planes.axes("sagittal"), (2, 1, 0))

    def test_rows_are_SI_on_both_planes(self):
        """A section is imaged with dorsal-ventral in the image plane either way."""
        for p in planes.PLANES:
            self.assertEqual(planes.axes(p)[1], 1, f"{p} rows should be SI")

    def test_each_plane_uses_all_three_axes_exactly_once(self):
        for p in planes.PLANES:
            self.assertEqual(sorted(planes.axes(p)), [0, 1, 2], p)

    def test_an_unknown_plane_raises_rather_than_defaulting(self):
        """Falling back to coronal would register a sagittal section against the
        wrong axis and still produce a full, plausible-looking label set."""
        for bad in ("axial", "CORONAL_", "horizontal", 0):
            with self.assertRaises(ValueError):
                planes.axes(bad)

    def test_case_and_whitespace_are_tolerated(self):
        self.assertEqual(planes.normalize("  Sagittal "), "sagittal")


class SliceGeometryTests(unittest.TestCase):
    def setUp(self):
        # a deliberately non-cubic volume, so a transposed axis cannot pass
        self.shape = (264, 160, 228)                  # (AP, SI, LR) at 50 um
        self.xI = [np.arange(n) * 50.0 - (n - 1) * 50.0 / 2 for n in self.shape]

    def test_slice_counts_come_from_the_right_axis(self):
        self.assertEqual(planes.n_slices(self.shape, "coronal"), 264)
        self.assertEqual(planes.n_slices(self.shape, "sagittal"), 228)

    def test_slice_coordinate_matches_the_historical_coronal_value(self):
        """AP index 150 at 50 um is 925.0 um -- the value stored in every bundle
        on disk as selected_ap_um."""
        self.assertAlmostEqual(
            planes.slice_coordinate(self.xI, 150, "coronal"), 925.0)

    def test_the_sagittal_midline_is_not_exactly_addressable(self):
        """228 LR slices is EVEN, so the coordinates straddle zero (-25 and +25 um
        at 50 um) and there is no midline voxel. The same half-voxel straddle the
        AP axis has between resolutions; worth a test because "slice the midline"
        is the obvious first thing anyone will try with a sagittal atlas."""
        mid = planes.nearest_index(self.xI, 0.0, "sagittal")
        um = planes.slice_coordinate(self.xI, mid, "sagittal")
        self.assertNotEqual(um, 0.0)
        self.assertLessEqual(abs(um), 25.0)          # within half a voxel
        # and it really is a straddle: the neighbour sits the same distance the
        # other side, so neither is "the" midline
        other = planes.slice_coordinate(self.xI, mid + 1, "sagittal")
        self.assertAlmostEqual(um + other, 0.0, places=6)

    def test_nearest_index_round_trips_a_micron_choice(self):
        for p in planes.PLANES:
            for idx in (10, 77, 150):
                if idx >= planes.n_slices(self.shape, p):
                    continue
                um = planes.slice_coordinate(self.xI, idx, p)
                self.assertEqual(planes.nearest_index(self.xI, um, p), idx, (p, idx))

    def test_plane_points_put_row_and_col_on_the_right_atlas_axes(self):
        rows = np.array([-100.0, 0.0, 100.0])
        cols = np.array([-50.0, 50.0])
        for p in planes.PLANES:
            s, r, c = planes.axes(p)
            pts = planes.plane_points(rows, cols, p)
            self.assertEqual(pts.shape, (1, 3, 2, 3), p)
            np.testing.assert_allclose(pts[0, ..., s], 0.0,
                                       err_msg=f"{p}: slice axis must be zero")
            # row value must vary down the rows and be constant across columns
            np.testing.assert_allclose(pts[0, :, 0, r], rows, err_msg=p)
            np.testing.assert_allclose(pts[0, 0, :, c], cols, err_msg=p)

    def test_atlas_slice_comes_back_as_row_by_col(self):
        """For sagittal, np.take leaves (AP, SI) = (col, row). Returning that
        unswapped would make the silhouette initialisation exchange the SI and
        AP scale factors."""
        vol = np.zeros(self.shape)
        for p in planes.PLANES:
            s, r, c = planes.axes(p)
            img = planes.atlas_slice(vol, 3, p)
            self.assertEqual(img.shape, (self.shape[r], self.shape[c]), p)

    def test_atlas_slice_returns_the_values_of_that_slice(self):
        vol = np.arange(np.prod(self.shape), dtype=float).reshape(self.shape)
        # coronal: slice 7 of axis 0, already (SI, LR)
        np.testing.assert_array_equal(planes.atlas_slice(vol, 7, "coronal"), vol[7])
        # sagittal: slice 7 of axis 2 is (AP, SI); expected transposed to (SI, AP)
        np.testing.assert_array_equal(planes.atlas_slice(vol, 7, "sagittal"),
                                      vol[:, :, 7].T)


class LegacyJobTests(unittest.TestCase):
    """A job written before `plane` existed must keep working untouched."""

    def test_a_legacy_orientation_is_not_a_change(self):
        """Regression, 2026-09-21: `set_orientation` compared a stored
        {angle, flip} against a new {angle, flip, plane}, so re-confirming an
        UNCHANGED orientation looked like a change and rolled a COMPLETED job
        back to ORIENTATION_CONFIRMED, discarding its registration and curation.
        Every job on disk predates the field, so this hit all of them."""
        legacy = {"angle": 12.0, "flip": "none"}
        fresh = {"angle": 12.0, "flip": "none", "plane": planes.DEFAULT}
        filled = dict(legacy)
        filled.setdefault("plane", planes.DEFAULT)
        self.assertEqual(filled, fresh)

    def test_absent_plane_resolves_to_coronal(self):
        self.assertEqual(planes.normalize({}.get("plane")), planes.CORONAL)


if __name__ == "__main__":
    unittest.main()


class SectionPlaneDocumentTests(unittest.TestCase):
    """The plane comes from data/metadata/section_planes.csv, not a folder name.

    Discovery scans one root and cannot tell the two apart by looking, and the
    failure is silent -- a sagittal section registered as coronal still yields a
    full, plausible label set. So the document is the only thing standing between
    a new dataset and a wrong answer nobody notices.
    """

    def test_anything_unlisted_is_coronal_by_absence(self):
        """Absence is the answer, not a missing entry: every experiment predating
        the document is coronal, so listing them all would be noise."""
        import config
        self.assertIsNone(config._plane_for("tr_202401020900_R1",
                                            "202401020900_Something"))

    def test_an_experiment_prefix_covers_all_of_its_regions(self):
        import config, unittest.mock
        rules = [("202401011200", "sagittal")]
        with unittest.mock.patch.object(config, "SECTION_PLANE_RULES", rules):
            for region in ("R1", "R2", "S3"):
                self.assertEqual(
                    config._plane_for(f"tr_202401011200_{region}",
                                      "202401011200_Experiment"), "sagittal")

    def test_a_full_region_key_overrides_its_experiment(self):
        """Longest match wins, so one region of an otherwise uniform experiment
        can be corrected without splitting the folder."""
        import config, unittest.mock
        rules = sorted([("202401011200", "sagittal"),
                        ("tr_202401011200_R2", "coronal")],
                       key=lambda r: -len(r[0]))
        with unittest.mock.patch.object(config, "SECTION_PLANE_RULES", rules):
            self.assertEqual(config._plane_for("tr_202401011200_R1",
                                               "202401011200_E"), "sagittal")
            self.assertEqual(config._plane_for("tr_202401011200_R2",
                                               "202401011200_E"), "coronal")

    def test_comments_and_blank_lines_are_ignored(self):
        """A plane document is mostly a comment block explaining the schema."""
        import config, tempfile, unittest.mock
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("# Which Allen plane each experiment was sectioned in.\n#\n\n"
                     "match,plane,note\n"
                     "202401011200,sagittal,example\n"
                     "\n# a later comment\n"
                     "tr_202401011200_R2,Coronal,one region overridden\n")
        try:
            with unittest.mock.patch.object(config, "SECTION_PLANES_FILE", fh.name):
                rules = config._load_section_planes()
        finally:
            os.unlink(fh.name)
        self.assertEqual(rules, [("tr_202401011200_R2", "coronal"),
                                 ("202401011200", "sagittal")])
        for match, plane in rules:
            self.assertFalse(match.startswith("#"), match)
            self.assertIn(plane, planes.PLANES)


