import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from allen_alignment.qc.m4_hex_qc import (assign_hex, compute_hex_qc, hex_vertices,
                                          _render_hex_map)


GENES = [f"Gene{i:02d}" for i in range(24)]


def _reference():
    return pd.DataFrame(
        [np.r_[np.full(12, 20.0), np.ones(12)],
         np.r_[np.ones(12), np.full(12, 20.0)]],
        index=["A", "B"], columns=GENES,
    )


def _tables(n_per_group=12):
    ids_a = [f"a{i}" for i in range(n_per_group)]
    ids_b = [f"b{i}" for i in range(n_per_group)]
    mapped = pd.DataFrame({
        "EntityID": ids_a + ids_b,
        "center_x": [0.0] * n_per_group + [1000.0] * n_per_group,
        "center_y": [0.0] * (2 * n_per_group),
        "allen_region_id": [1] * n_per_group + [2] * n_per_group,
        "allen_acronym": ["A"] * n_per_group + ["B"] * n_per_group,
    })
    a = np.r_[np.full(12, 20), np.ones(12)]
    b = np.r_[np.ones(12), np.full(12, 20)]
    cbg = pd.DataFrame(np.vstack([a] * n_per_group + [b] * n_per_group), columns=GENES)
    cbg.insert(0, "EntityID", ids_a + ids_b)
    return mapped, cbg


class HexGeometryTests(unittest.TestCase):
    def test_center_assignment_and_closed_vertices(self):
        size = 100.0
        x = np.array([0.0, np.sqrt(3.0) * size, np.sqrt(3.0) * size / 2.0])
        y = np.array([0.0, 0.0, 1.5 * size])
        q, r, cx, cy = assign_hex(x, y, size)
        np.testing.assert_array_equal(q, [0, 1, 0])
        np.testing.assert_array_equal(r, [0, 0, 1])
        np.testing.assert_allclose(cx, x)
        np.testing.assert_allclose(cy, y)
        ring = hex_vertices(cx[0], cy[0], size)
        self.assertEqual(ring.shape, (7, 2))
        np.testing.assert_allclose(ring[0], ring[-1])


class HexExpressionTests(unittest.TestCase):
    def test_distinct_spatial_bins_match_their_mapped_regions(self):
        mapped, cbg = _tables()
        result = compute_hex_qc(mapped, cbg, _reference(), size_um=100, min_cells=5)
        self.assertEqual(len(result), 2)
        by_region = result.set_index("mapped_region")
        self.assertEqual(set(by_region.index), {"A", "B"})
        self.assertTrue(bool(by_region.loc["A", "best_is_mapped"]))
        self.assertTrue(bool(by_region.loc["B", "best_is_mapped"]))
        self.assertGreater(float(by_region.loc["A", "self_corr"]), 0.9)
        self.assertGreater(float(by_region.loc["B", "self_corr"]), 0.9)

    def test_minimum_cell_filter_is_applied(self):
        mapped, cbg = _tables(n_per_group=4)
        result = compute_hex_qc(mapped, cbg, _reference(), size_um=100, min_cells=5)
        self.assertTrue(result.empty)

    def test_requires_enough_reference_genes(self):
        mapped, cbg = _tables()
        with self.assertRaisesRegex(ValueError, "need at least"):
            compute_hex_qc(mapped, cbg, _reference()[GENES[:10]], size_um=100, min_cells=5)


if __name__ == "__main__":
    unittest.main()


class HexRenderOrientationTests(unittest.TestCase):
    """The Results map is drawn dorsal-up, but binning stays in the raw MERFISH
    frame. Orientation must therefore change the PNG and nothing else."""

    def _table(self):
        mapped, cbg = _tables()
        return compute_hex_qc(mapped, cbg, _reference(), size_um=100, min_cells=5)

    def test_orientation_does_not_touch_the_scored_table(self):
        table = self._table()
        before = table.copy(deep=True)
        with tempfile.TemporaryDirectory() as d:
            _render_hex_map(table, 100.0, os.path.join(d, "r.png"),
                            angle=122.0, flip="y")
        pd.testing.assert_frame_equal(table, before)

    def test_rotation_is_rigid_and_changes_the_image(self):
        table = self._table()
        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "raw.png")
            rot = os.path.join(d, "rot.png")
            _render_hex_map(table, 100.0, raw)
            _render_hex_map(table, 100.0, rot, angle=90.0, flip=None)
            self.assertTrue(os.path.getsize(raw) > 0)
            self.assertNotEqual(open(raw, "rb").read(), open(rot, "rb").read())

    def test_pure_mirror_is_applied_even_with_a_zero_angle(self):
        """Regression, 2026-09-16: the renderer gated the whole transform on
        `if angle`, so a step-2 pick of angle 0 with flip "x" -- a mirror and no
        rotation, which is an ordinary pick -- was silently dropped and the map
        came out in the raw frame, mirrored against every other Results figure.
        """
        table = self._table()
        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "raw.png")
            mirrored = os.path.join(d, "mirror.png")
            _render_hex_map(table, 100.0, raw)
            _render_hex_map(table, 100.0, mirrored, angle=0.0, flip="x")
            self.assertNotEqual(open(raw, "rb").read(), open(mirrored, "rb").read(),
                                "a flip with no rotation must still be drawn")

    def test_the_mirror_is_a_real_reflection_not_just_a_different_image(self):
        """(x, y) -> (x, -y) is what flip "x" means. Reaching the same mapping
        the long way round -- rotate 180 to (-x, -y), then flip "y" to negate x
        back -- must produce the identical picture, which a merely-different
        image would not."""
        table = self._table()
        with tempfile.TemporaryDirectory() as d:
            direct = os.path.join(d, "direct.png")
            roundabout = os.path.join(d, "roundabout.png")
            _render_hex_map(table, 100.0, direct, angle=0.0, flip="x")
            _render_hex_map(table, 100.0, roundabout, angle=180.0, flip="y")
            self.assertEqual(open(direct, "rb").read(),
                             open(roundabout, "rb").read())

    def test_zero_angle_is_a_no_op(self):
        """Still true after the mirror fix: no rotation AND no flip means no
        transform. It is `angle=0, flip="x"` that must not be a no-op."""
        table = self._table()
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a.png")
            b = os.path.join(d, "b.png")
            _render_hex_map(table, 100.0, a)
            _render_hex_map(table, 100.0, b, angle=0.0, flip=None)
            self.assertEqual(open(a, "rb").read(), open(b, "rb").read())

