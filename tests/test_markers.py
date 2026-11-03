"""Tests for the anatomical marker-gene background (allen_alignment.markers).

The layer makes an anatomical claim on screen, so the things worth pinning are
the ones that would make it quietly WRONG rather than merely ugly:

  * cell_by_gene is joined to the bundle's cell order on EntityID. If that join
    were positional and the two files disagreed, every marker would be smeared
    across the section and the picture would still look plausible.
  * a division is only drawn when enough of its markers are in the panel.
  * the colours are the atlas overlay's own colours -- that is the entire reason
    the layer is useful for judging alignment.
"""
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

import numpy as np                                  # noqa: E402
import pandas as pd                                 # noqa: E402
from allen_alignment import markers                 # noqa: E402
from allen_alignment.atlas import region_style      # noqa: E402


class TestMarkerTable(unittest.TestCase):
    def test_table_loads_and_every_region_has_markers(self):
        m = markers.load_markers()
        self.assertGreaterEqual(len(m), 10)
        for acro, spec in m.items():
            self.assertTrue(spec["genes"], f"{acro} has no markers")
            self.assertEqual(len(set(spec["genes"])), len(spec["genes"]),
                             f"{acro} lists a gene twice")

    def test_colours_come_from_the_atlas_not_from_this_module(self):
        """The whole point: marker colour == overlay colour for that division."""
        for r in markers.match_panel([g for s in markers.load_markers().values()
                                      for g in s["genes"]]):
            self.assertEqual(r["color"], region_style(r["acronym"])["color"])
            self.assertEqual(r["short"], region_style(r["acronym"])["short"])

    def test_region_acronyms_are_real_allen_divisions(self):
        """A typo here would silently render a division in the fallback grey."""
        from allen_alignment.atlas import COARSE_REGION_STYLE
        known = set(COARSE_REGION_STYLE) | {"fiber tracts"}
        for acro in markers.load_markers():
            self.assertIn(acro, known, f"{acro} is not a known division")


class TestPanelMatching(unittest.TestCase):
    def test_gene_matching_is_case_insensitive(self):
        m = {"TH": {"name": "Thalamus", "genes": ["Tcf7l2", "Slc17a6", "Prkcd"]}}
        rep = markers.match_panel(["cell", "TCF7L2", "slc17a6", "PrKcD"], m)
        self.assertEqual(rep[0]["n_present"], 3)
        # the PANEL's spelling is returned, because that is the column to read
        self.assertEqual(sorted(rep[0]["present"]), ["PrKcD", "TCF7L2", "slc17a6"])

    def test_thin_region_is_reported_but_not_drawn(self):
        m = {"CB": {"name": "Cerebellum", "genes": ["Gabra6", "Calb1", "Car8", "Zic1"]}}
        rep = markers.match_panel(["cell", "Gabra6", "Calb1"], m)
        self.assertEqual(rep[0]["n_present"], 2)
        self.assertFalse(rep[0]["used"], "2 genes is below MIN_GENES_PER_REGION")
        self.assertEqual(sorted(rep[0]["absent"]), ["Car8", "Zic1"])

    def test_absent_region_reports_every_gene_missing(self):
        m = {"HY": {"name": "Hypothalamus", "genes": ["Agrp", "Pomc"]}}
        rep = markers.match_panel(["cell", "Slc17a7"], m)
        self.assertEqual(rep[0]["n_present"], 0)
        self.assertFalse(rep[0]["used"])


# The raw 50 um binning: one bin per plane pixel, no kernel. The tests below are
# about the EntityID join, the bounds handling and the colours, so they pin this
# rather than the shipped display grid -- otherwise a change to PLANE_SUPERSAMPLE
# or KDE_SIGMA_UM would fail four tests that have nothing to do with either.
RAW = {"supersample": 1, "sigma_um": 0}


class TestPlanesAndComposite(unittest.TestCase):
    """Drive region_planes against a synthetic experiment with known geometry."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.exp = self._tmp.name
        # 4 cells: two in the left half, two in the right
        pd.DataFrame({"EntityID": [10, 11, 12, 13],
                      "center_x": [0.0, 1.0, 2.0, 3.0],
                      "center_y": [0.0, 0.0, 0.0, 0.0]}).to_csv(
            os.path.join(self.exp, "cell_metadata.csv"), index=False)
        # DELIBERATELY a different row order from cell_metadata: a positional
        # join would put the cortex signal on the wrong cells.
        pd.DataFrame({"cell": [13, 12, 11, 10],
                      "Slc17a7": [0, 0, 5, 5],
                      "Cux2": [0, 0, 5, 5],
                      "Rorb": [0, 0, 5, 5],
                      "Tcf7l2": [7, 7, 0, 0],
                      "Slc17a6": [7, 7, 0, 0],
                      "Prkcd": [7, 7, 0, 0]}).to_csv(
            os.path.join(self.exp, "cell_by_gene.csv"), index=False)
        self.bundle = {
            "Jimg": np.zeros((1, 4), float),
            "row": np.array([0, 0, 0, 0]),
            "col": np.array([0, 1, 2, 3]),
            "inb": np.array([True, True, True, True]),
            "exp_path": self.exp,
        }
        self.mk = {
            "Isocortex": {"name": "Isocortex", "genes": ["Slc17a7", "Cux2", "Rorb"]},
            "TH": {"name": "Thalamus", "genes": ["Tcf7l2", "Slc17a6", "Prkcd"]},
        }

    def tearDown(self):
        self._tmp.cleanup()

    def test_join_is_on_entity_id_not_row_order(self):
        planes, _ = markers.region_planes(self.bundle, self.exp, self.mk, **RAW)
        # cells 10,11 (grid cols 0,1) are cortex; 12,13 (cols 2,3) are thalamus
        np.testing.assert_allclose(planes["Isocortex"][0], [15, 15, 0, 0])
        np.testing.assert_allclose(planes["TH"][0], [0, 0, 21, 21])

    def test_out_of_bounds_cells_are_dropped_not_wrapped(self):
        b = dict(self.bundle)
        b["col"] = np.array([0, 1, 2, 99])          # last cell off the grid
        planes, _ = markers.region_planes(b, self.exp, self.mk, **RAW)
        self.assertEqual(planes["TH"][0].sum(), 21.0, "only the in-bounds cell counts")

    def test_inb_false_is_respected(self):
        b = dict(self.bundle)
        b["inb"] = np.array([True, True, False, False])
        planes, _ = markers.region_planes(b, self.exp, self.mk, **RAW)
        self.assertEqual(planes["TH"][0].sum(), 0.0)

    def test_supersample_scales_the_grid_and_keeps_the_footprint(self):
        """3x must subdivide each plane pixel, not shift or re-scale the layer.

        The layer is drawn over the same extent as DAPI and cell density, so a
        half-pixel slip here would show up as the backgrounds jumping when the
        user toggles between them -- the one thing this module promises not to do.
        """
        raw, _ = markers.region_planes(self.bundle, self.exp, self.mk, **RAW)
        fine, _ = markers.region_planes(self.bundle, self.exp, self.mk,
                                        supersample=3, sigma_um=0)
        self.assertEqual(raw["TH"].shape, (1, 4))
        self.assertEqual(fine["TH"].shape, (3, 12))
        # each coarse pixel's total must land inside ITS OWN block of 3x3
        blocks = fine["TH"].reshape(1, 3, 4, 3).sum(axis=(1, 3))
        np.testing.assert_allclose(blocks, raw["TH"])

    def test_supersample_one_reproduces_the_original_binning(self):
        """The pre-2026-09-16 behaviour is still reachable, exactly."""
        planes, _ = markers.region_planes(self.bundle, self.exp, self.mk,
                                          supersample=1, sigma_um=0)
        np.testing.assert_allclose(planes["Isocortex"][0], [15, 15, 0, 0])
        np.testing.assert_allclose(planes["TH"][0], [0, 0, 21, 21])

    def test_the_kernel_conserves_total_signal(self):
        """Smoothing must move signal around, never create or destroy it --
        otherwise the per-division percentile normalisation downstream is
        measuring the kernel rather than the panel."""
        raw, _ = markers.region_planes(self.bundle, self.exp, self.mk, **RAW)
        smooth, _ = markers.region_planes(self.bundle, self.exp, self.mk,
                                          supersample=3, sigma_um=40.0)
        for acro in raw:
            self.assertAlmostEqual(float(smooth[acro].sum()),
                                   float(raw[acro].sum()), places=6,
                                   msg=f"{acro} gained or lost signal")

    def test_the_kernel_actually_spreads_the_signal(self):
        """A guard against sigma silently becoming a no-op: after smoothing, a
        division must occupy more pixels than the cells it came from."""
        sharp, _ = markers.region_planes(self.bundle, self.exp, self.mk,
                                         supersample=3, sigma_um=0)
        smooth, _ = markers.region_planes(self.bundle, self.exp, self.mk,
                                          supersample=3, sigma_um=40.0)
        self.assertGreater(int((smooth["TH"] > 1e-9).sum()),
                           int((sharp["TH"] > 1e-9).sum()))

    def test_continuous_coordinates_place_cells_within_a_plane_pixel(self):
        """With xr/yr present the kernel sees where a cell actually is, not which
        50 um box it fell in. Two cells in the SAME coarse pixel but at opposite
        ends of it must land in different sub-pixels."""
        b = dict(self.bundle)
        b["Jimg"] = np.zeros((1, 2), float)
        b["row"] = np.array([0, 0, 0, 0])
        b["col"] = np.array([0, 0, 1, 1])
        b["r0"] = 0.0
        b["c0"] = 0.0
        b["drow"] = 50.0
        b["dcol"] = 50.0
        # cells 10 and 11 both round to col 0, but sit at -20 um and +20 um
        b["yr"] = np.array([0.0, 0.0, 0.0, 0.0])
        b["xr"] = np.array([-20.0, 20.0, 50.0, 50.0])
        planes, _ = markers.region_planes(b, self.exp, self.mk,
                                          supersample=3, sigma_um=0)
        # the single coarse row becomes fine rows 0..2, so collapse them rather
        # than guessing which one holds the centre
        self.assertEqual(planes["Isocortex"].shape, (3, 6))
        cortex = planes["Isocortex"].sum(axis=0)
        # both cortex cells are in coarse pixel 0 -> sub-pixels 0..2, and they
        # must NOT have collapsed onto the same one
        self.assertEqual(int((cortex > 0).sum()), 2)
        np.testing.assert_allclose(cortex[3:], 0)

    def test_composite_colours_each_territory_with_its_allen_colour(self):
        planes, report = markers.region_planes(self.bundle, self.exp, self.mk, **RAW)
        rgba = markers.composite_rgba(planes, report)
        self.assertEqual(rgba.shape, (1, 4, 4))
        self.assertEqual(rgba.dtype, np.uint8)

        def near(px, hexcol):
            want = np.array([int(hexcol[i:i + 2], 16) for i in (1, 3, 5)], float)
            return float(np.abs(px[:3].astype(float) - want).max())

        self.assertLess(near(rgba[0, 0], region_style("Isocortex")["color"]), 12)
        self.assertLess(near(rgba[0, 3], region_style("TH")["color"]), 12)
        self.assertTrue((rgba[..., 3] > 0).all(), "every cell-bearing pixel is opaque")

    def test_only_restricts_which_divisions_are_rendered(self):
        """The legend toggles. The REPORT still describes the whole panel, so a
        switched-off division can still be offered in the legend."""
        planes, report = markers.region_planes(
            self.bundle, self.exp, self.mk, only={"TH"}, **RAW)
        self.assertEqual(set(planes), {"TH"})
        self.assertEqual({r["acronym"] for r in report}, {"Isocortex", "TH"},
                         "the report must still list every division")
        np.testing.assert_allclose(planes["TH"][0], [0, 0, 21, 21])

    def test_only_with_nothing_selected_renders_nothing(self):
        planes, _ = markers.region_planes(self.bundle, self.exp, self.mk, only=set(), **RAW)
        self.assertEqual(planes, {})
        self.assertIsNone(markers.composite_rgba(planes, []))

    def test_empty_planes_render_nothing_rather_than_crashing(self):
        self.assertIsNone(markers.composite_rgba({}, []))

    def test_missing_cell_by_gene_is_a_clear_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(FileNotFoundError) as cm:
                markers.region_planes(self.bundle, empty, self.mk)
            self.assertIn("cell_by_gene", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
