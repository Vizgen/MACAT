import unittest
from unittest.mock import patch

import numpy as np

import pipeline_api as api
from allen_alignment import atlas as _atlas
from allen_alignment import boundaries as _bnd

# Patch targets are the OWNING modules, not the pipeline_api facade. Since the
# core was split (2026-09-14) a function resolves its helpers in its own module
# namespace, so patching the facade's re-exported binding would silently have no
# effect -- these tests would pass against the real structure tree instead of the
# fixture. `_bnd` is where the boundary code looks up the plane helpers it
# imported; `_atlas` is where the hierarchy roll-up looks up the structure tree.


class Depth2PolygonTests(unittest.TestCase):
    def test_rollup_uses_requested_literal_hierarchy_level(self):
        tree = {
            101: {"acronym": "leaf", "name": "leaf", "color": "#111111",
                  "path": [997, 10, 20, 30, 101]},
            10: {"acronym": "D1", "name": "depth one", "color": "#101010",
                 "path": [997, 10]},
            20: {"acronym": "D2", "name": "depth two", "color": "#202020",
                 "path": [997, 10, 20]},
            30: {"acronym": "D3", "name": "depth three", "color": "#303030",
                 "path": [997, 10, 20, 30]},
        }
        bundle = {"AphiL": np.array([[101, 101]], dtype=np.int32)}
        with patch.object(_atlas, "_load_structure_tree", return_value=tree):
            depth2, meta2 = api._group_by_depth2(bundle, level=2)
            depth3, meta3 = api._group_by_depth2(bundle, level=3)
        np.testing.assert_array_equal(depth2, [[20, 20]])
        np.testing.assert_array_equal(depth3, [[30, 30]])
        self.assertEqual(meta2[20]["acronym"], "D2")
        self.assertEqual(meta3[30]["color"], "#303030")

    def test_component_exterior_and_hole_are_closed(self):
        mask = np.zeros((24, 30), bool)
        mask[2:20, 3:24] = True
        mask[8:14, 10:17] = False
        rings = api._closed_mask_rings(mask, tolerance_px=0, max_pts=400)
        self.assertEqual([r["role"] for r in rings], ["exterior", "hole"])
        for ring in rings:
            pts = ring["points"]
            self.assertGreaterEqual(len(pts), 4)
            np.testing.assert_array_equal(pts[0], pts[-1])

    def test_edge_touching_component_is_still_closed(self):
        mask = np.zeros((12, 16), bool)
        mask[:8, :7] = True
        rings = api._closed_mask_rings(mask, tolerance_px=0, max_pts=400)
        self.assertEqual(len(rings), 1)
        np.testing.assert_array_equal(
            rings[0]["points"][0], rings[0]["points"][-1])

    def test_bilateral_components_remain_separate_polygons(self):
        idmap = np.zeros((24, 40), np.int32)
        idmap[3:20, 3:14] = 10
        idmap[3:20, 26:37] = 10
        idmap[8:13, 7:10] = 0
        meta = {10: {"acronym": "VL", "name": "lateral ventricle",
                     "color": "#9aa6b2"}}
        clean_result = (idmap, meta, idmap > 0, 1.0)

        with patch.object(_bnd, "_depth2_clean", return_value=clean_result), \
             patch.object(_bnd, "_plane_spacing",
                          return_value=(None, None, 1.0, 1.0)), \
             patch.object(_bnd, "_px_to_um",
                          side_effect=lambda bundle, pts: np.asarray(pts)[:, ::-1]):
            features = api.depth2_boundaries(
                {}, outer=False, midline=False, max_pts=400)

        self.assertEqual(len(features), 2)
        self.assertTrue(all(f["geometry"] == "polygon" for f in features))
        self.assertTrue(all(f["acronym"] == "VL" for f in features))
        self.assertEqual({f["component"] for f in features}, {1, 2})
        self.assertTrue(any(
            any(r["role"] == "hole" for r in f["rings"]) for f in features))
        for feature in features:
            self.assertNotIn("points", feature)
            self.assertEqual(feature["rings"][0]["role"], "exterior")
            for ring in feature["rings"]:
                self.assertEqual(ring["points"][0], ring["points"][-1])

    def test_every_region_component_carries_its_measured_area(self):
        """Area is measured once, in the backend, on the same cleaned raster the
        rings were traced from. The curation legend orders its pills by it, so it
        must be present on EVERY drawn component -- including the ones too small
        to earn a label -- and it must exclude enclosed holes."""
        idmap = np.zeros((24, 40), np.int32)
        idmap[3:20, 3:14] = 10          # 17 x 11 = 187 px, minus a 5 x 3 hole
        idmap[3:20, 26:37] = 10         # 17 x 11 = 187 px, solid
        idmap[8:13, 7:10] = 0
        idmap[21:23, 1:3] = 20          # 2 x 2 sliver: far below any label cutoff
        meta = {10: {"acronym": "VL", "name": "lateral ventricle",
                     "color": "#9aa6b2"},
                20: {"acronym": "eps", "name": "sliver", "color": "#123456"}}
        clean_result = (idmap, meta, idmap > 0, 2.0)   # 2 um^2 per pixel

        with patch.object(_bnd, "_depth2_clean", return_value=clean_result), \
             patch.object(_bnd, "_plane_spacing",
                          return_value=(None, None, 1.0, 1.0)), \
             patch.object(_bnd, "_px_to_um",
                          side_effect=lambda bundle, pts: np.asarray(pts)[:, ::-1]):
            features = api.depth2_boundaries(
                {}, outer=False, midline=False, max_pts=400)

        regions = [f for f in features if f["kind"] == "region"]
        self.assertEqual(len(regions), 3)
        for feature in regions:
            self.assertIn("area_um2", feature)
        areas = sorted(f["area_um2"] for f in regions)
        # holed component 172 px, solid 187 px, sliver 4 px -- all x 2 um^2
        self.assertEqual(areas, [8.0, 344.0, 374.0])

    def test_selected_hierarchy_level_still_returns_closed_polygons(self):
        idmap = np.zeros((20, 28), np.int32)
        idmap[2:17, 3:24] = 30
        idmap[7:12, 9:16] = 0
        meta = {30: {"acronym": "D3", "name": "depth three", "color": "#303030"}}
        clean_result = (idmap, meta, idmap > 0, 1.0)
        with patch.object(_bnd, "_depth2_clean", return_value=clean_result) as clean, \
             patch.object(_bnd, "_plane_spacing", return_value=(None, None, 1.0, 1.0)), \
             patch.object(_bnd, "_px_to_um",
                          side_effect=lambda bundle, pts: np.asarray(pts)[:, ::-1]):
            features = api.depth2_boundaries(
                {}, outer=False, midline=False, max_pts=400, level=3)
        clean.assert_called_once_with({}, level=3)
        self.assertEqual(len(features), 1)
        self.assertTrue(any(r["role"] == "hole" for r in features[0]["rings"]))
        for ring in features[0]["rings"]:
            self.assertEqual(ring["points"][0], ring["points"][-1])

    def test_alignment_union_emits_one_real_exterior_per_component(self):
        A = np.zeros((30, 50), np.int32)
        A[5:22, 4:18] = 101
        A[8:18, 8:14] = 102
        A[5:22, 32:46] = 101
        A[8:18, 36:42] = 102
        tree = {
            10: {"acronym": "HPF", "name": "hippocampal formation",
                 "color": "#88aaff", "path": [997, 10]},
            101: {"acronym": "CA1", "name": "field CA1",
                  "color": "#88aaff", "path": [997, 10, 101]},
            102: {"acronym": "DG", "name": "dentate gyrus",
                  "color": "#88aaff", "path": [997, 10, 102]},
        }
        # alignment_region_boundaries reads the tree through the binding
        # boundaries.py imported, so the patch belongs there -- unlike the
        # roll-up test above, where _group_by_depth2 resolves it inside atlas.py.
        with patch.object(_bnd, "_load_structure_tree", return_value=tree), \
             patch.object(_bnd, "_plane_spacing",
                          return_value=(None, None, 1.0, 1.0)), \
             patch.object(_bnd, "_px_to_um",
                          side_effect=lambda bundle, pts: np.asarray(pts)[:, ::-1]):
            features = api.alignment_region_boundaries(
                {"AphiL": A}, outer=False, max_pts=400)

        self.assertEqual(len(features), 2)
        self.assertTrue(all(f["acronym"] == "HPF" for f in features))
        self.assertTrue(all(f["kind"] == "alignment_region" for f in features))
        self.assertTrue(all(f["members"] == ["CA1", "DG"] for f in features))
        self.assertEqual({f["component"] for f in features}, {1, 2})
        for feature in features:
            self.assertEqual(len(feature["rings"]), 1)
            self.assertEqual(feature["rings"][0]["role"], "exterior")
            self.assertEqual(
                feature["rings"][0]["points"][0],
                feature["rings"][0]["points"][-1],
            )


if __name__ == "__main__":
    unittest.main()
