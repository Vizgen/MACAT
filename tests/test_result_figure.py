"""The curated result figure's roll-up and colour helpers.

The figure itself needs a bundle and matplotlib, so what is pinned here is the
part that can be silently wrong while still rendering: which ancestor a cell is
rolled up to, and what colour it gets.
"""
import os
import sys
import unittest

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import numpy as np                                              # noqa: E402
from allen_alignment import figures                             # noqa: E402


TREE = {
    101: {"acronym": "leaf", "name": "leaf", "color": "#112233",
          "path": [997, 10, 20, 30, 101]},          # depth 4
    102: {"acronym": "deep", "name": "deep", "color": "#445566",
          "path": [997, 10, 20, 30, 40, 102]},      # depth 5
    103: {"acronym": "shallow", "name": "shallow", "color": "#778899",
          "path": [997, 10, 103]},                  # depth 2
    30: {"acronym": "D3", "name": "d3", "color": "#AABBCC", "path": [997, 10, 20, 30]},
    20: {"acronym": "D2", "name": "d2", "color": "#DDEEFF", "path": [997, 10, 20]},
}


class RollupTests(unittest.TestCase):
    def test_cells_roll_up_to_the_requested_depth(self):
        rid = np.array([101, 102, 101, 0])
        out = figures._rollup(rid, 3, TREE)
        np.testing.assert_array_equal(out, [30, 30, 30, 0])

    def test_a_structure_shallower_than_the_depth_gets_no_ancestor(self):
        """_ancestor_at_depth deliberately does not clamp: a region annotated at
        depth 2 has no depth-4 ancestor, and inventing one would put cells in a
        structure the atlas never said they were in. They must come back 0 so the
        panel can count them."""
        rid = np.array([103, 101])
        out = figures._rollup(rid, 4, TREE)
        np.testing.assert_array_equal(out, [0, 101])

    def test_unlabelled_cells_stay_unlabelled(self):
        np.testing.assert_array_equal(figures._rollup(np.zeros(3, int), 2, TREE),
                                      [0, 0, 0])

    def test_rolling_up_never_increases_the_distinct_region_count(self):
        rid = np.array([101, 102, 103])
        deep = len(set(figures._rollup(rid, 4, TREE)) - {0})
        shallow = len(set(figures._rollup(rid, 2, TREE)) - {0})
        self.assertLessEqual(shallow, deep + 1)


class ColourTests(unittest.TestCase):
    def test_cells_take_their_allen_colour(self):
        rid = np.array([101, 102])
        colr = figures._cell_colors(rid, TREE)
        np.testing.assert_allclose(colr[0][:3], [0x11 / 255, 0x22 / 255, 0x33 / 255],
                                   atol=1e-6)
        self.assertAlmostEqual(colr[0][3], 0.9)

    def test_unmapped_cells_are_faint_grey_not_a_region_colour(self):
        colr = figures._cell_colors(np.array([0]), TREE)
        np.testing.assert_allclose(colr[0], figures.UNMAPPED_RGBA)

    def test_an_id_the_tree_does_not_know_falls_back(self):
        """A leaf missing from the structure tree would otherwise silently render
        as unmapped grey, hiding a real population."""
        colr = figures._cell_colors(np.array([999]), TREE, fallback={999: "#FF0000"})
        np.testing.assert_allclose(colr[0][:3], [1.0, 0.0, 0.0], atol=1e-6)

    def test_legend_is_largest_first_and_capped(self):
        rid = np.array([101] * 5 + [102] * 9 + [103])
        rows = figures._legend_rows(rid, TREE, limit=2)
        self.assertEqual([r[0] for r in rows], ["deep", "leaf"])
        self.assertEqual([r[1] for r in rows], [9, 5])


class ConstantsTests(unittest.TestCase):
    def test_the_overlay_depth_and_dpi_are_what_results_claims(self):
        self.assertEqual(figures.RESULT_OVERLAY_DEPTH, 4)
        self.assertEqual(figures.RESULT_DPI, 300)

    def test_the_rolled_up_cell_panels_are_switched_off(self):
        """Emptied, not deleted (2026-09-21). _rollup and the layout still handle
        them, so this is the one line that turns them back on -- and the roll-up
        tests above keep working either way."""
        self.assertEqual(figures.CELL_PANEL_DEPTHS, ())

    def test_the_rollup_machinery_still_works_while_switched_off(self):
        """The panels are off; the code they used must not rot, or turning them
        back on later would be a rewrite rather than a one-line change."""
        out = figures._rollup(np.array([101, 102]), 3, TREE)
        np.testing.assert_array_equal(out, [30, 30])


class ExportResolutionTests(unittest.TestCase):
    """Every file the figure writes must be saved at RESULT_DPI.

    Source-level rather than by rendering, because rendering needs a bundle and
    an atlas, and the bug this guards against is a one-word omission at a call
    site. It was a real omission: the SVG export was written without `dpi`, so
    matplotlib used its default 100 and embedded the DAPI / marker background at
    1090x620 where the PNG had 3330x1978. The vectors were fine, which is exactly
    why nobody would have noticed.
    """

    def _savefig_calls(self):
        import ast
        src = os.path.join(_STUDY, "allen_alignment", "figures.py")
        with open(src) as fh:
            tree = ast.parse(fh.read())
        calls = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "savefig"):
                calls.append(node)
        return calls

    def test_there_are_savefig_calls_to_check(self):
        """Guard the guard: if the figure stops using savefig, the test below
        would pass vacuously."""
        self.assertGreaterEqual(len(self._savefig_calls()), 3)

    def test_every_savefig_passes_an_explicit_dpi(self):
        for call in self._savefig_calls():
            names = {kw.arg for kw in call.keywords}
            self.assertIn("dpi", names,
                          f"savefig at line {call.lineno} has no dpi; it will "
                          f"silently use matplotlib's default, not RESULT_DPI")

    def test_every_savefig_uses_the_module_constant(self):
        """A literal 300 would drift from RESULT_DPI the first time it changes."""
        import ast
        for call in self._savefig_calls():
            for kw in call.keywords:
                if kw.arg == "dpi":
                    self.assertIsInstance(
                        kw.value, ast.Name,
                        f"savefig at line {call.lineno} hard-codes dpi instead "
                        f"of using RESULT_DPI")
                    self.assertEqual(kw.value.id, "RESULT_DPI")


if __name__ == "__main__":
    unittest.main()
