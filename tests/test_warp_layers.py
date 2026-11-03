"""Stacked warp layers.

A curation may bake the current lattice and carry on with a fresh one. The stack
is applied in order, and finalize inverts it in REVERSE, one layer at a time --
which is the whole point: Newton only ever faces a gentle field instead of one
extreme one whose basin of convergence has closed up.

Every failure mode here is silent. A reversed inversion still produces a warp,
just the wrong one. A stack that composes into a fold is made of layers that are
each individually legal. And a single-lattice job must keep behaving exactly as
it did, because the overwhelming majority of saved curations are one lattice.
"""
import glob
import os
import sys
import unittest

_pre = os.path.join(sys.prefix, "lib", "libstdc++.so.6")
if os.path.exists(_pre) and _pre not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = _pre + (
        ":" + os.environ["LD_PRELOAD"] if os.environ.get("LD_PRELOAD") else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)

import numpy as np  # noqa: E402

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import pipeline_api as api  # noqa: E402
from allen_alignment.transforms import _invert_chain  # noqa: E402


def _bundle():
    # only a bundle at the configured resolution; see tests/_bundles.py
    from _bundles import first_bundle
    p = first_bundle()
    return api.load_bundle(p) if p else None


def _cap_axis(ext, rows=13, cols=13):
    """The axis whose node spacing sets the displacement cap: the SHORTER one.

    warp_max_disp is a fraction of the smaller spacing, so a push of `cap` along
    this axis is the strongest legal compression a layer can make. Along the
    other axis the same push is gentler, by the aspect ratio: on a wide sagittal
    extent, eight such layers stop at |J| ~0.1, right on the floor, and whether
    they cross it depended on which bundle the store held (2026-10-20). Along
    this axis they reach ~0.01 on every bundle measured."""
    lr0, lr1, si0, si1 = ext
    return 0 if (lr1 - lr0) / (cols - 1) <= (si1 - si0) / (rows - 1) else 1


_B = _bundle()


class NormaliseTests(unittest.TestCase):
    """warp_layers is what lets every consumer see one shape."""

    LAT = {"src": [[0, 0]], "dst": [[1, 1]]}

    def test_the_three_shapes_that_reach_it(self):
        self.assertEqual(warp_n(None), 0)
        self.assertEqual(warp_n(self.LAT), 1, "a bare lattice is a stack of one")
        self.assertEqual(warp_n({"layers": [self.LAT] * 3}), 3)

    def test_layers_without_geometry_are_dropped_not_applied(self):
        """An empty lattice applied as identity would be harmless; applied with
        a missing dst it would throw inside finalize, after the user confirmed."""
        self.assertEqual(warp_n({"layers": [{"src": [], "dst": []}, self.LAT]}), 1)
        self.assertEqual(warp_n({"layers": [{"src": [[0, 0]]}, self.LAT]}), 1)


def warp_n(w):
    return len(api.warp_layers(w))


@unittest.skipUnless(_B is not None, "no registration bundle at the configured resolution; see tests/_bundles.py")
class ChainGeometryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.b = _B
        cls.ext = list(api.warp_grid_extent(cls.b))
        cls.src = np.asarray(api.warp_grid_nodes(cls.b), float)
        cls.R = api.warp_grid_radius(cls.b)
        kinds = np.asarray(api.warp_node_kinds(13, 13))
        free = np.where(kinds == api.WARP_NODE_FREE)[0]
        cls.node = int(free[len(free) // 2])
        cls.cap = api.warp_max_disp(cls.ext, 13, 13, None)

    def lay(self, amt, axis=0):
        d = self.src.copy()
        d[self.node, axis] += amt
        return {"src": self.src, "dst": d, "R": self.R}

    def test_the_chain_round_trips(self):
        """Forward the stack, invert it, land back where you started."""
        layers = [self.lay(self.cap * 0.95),
                  self.lay(self.cap * 0.95, 1),
                  self.lay(-self.cap * 0.6)]
        lr0, lr1, si0, si1 = self.ext
        gx, gy = np.meshgrid(np.linspace(lr0 + 200, lr1 - 200, 40),
                             np.linspace(si0 + 200, si1 - 200, 40))
        P = np.c_[gx.ravel(), gy.ravel()]
        X = api.warp_chain_points(layers, P)
        back, st = _invert_chain(layers, X)
        self.assertEqual(st["n_bad"], 0)
        self.assertLess(np.hypot(*(back - P).T).max(), 1e-3)

    def test_inverting_in_the_wrong_order_is_caught_by_that_round_trip(self):
        """Guard the guard. If these layers commuted, the test above would pass
        whatever order the inverse used, and would be proving nothing."""
        layers = [self.lay(self.cap * 0.95),
                  self.lay(self.cap * 0.95, 1),
                  self.lay(-self.cap * 0.6)]
        lr0, lr1, si0, si1 = self.ext
        gx, gy = np.meshgrid(np.linspace(lr0 + 200, lr1 - 200, 30),
                             np.linspace(si0 + 200, si1 - 200, 30))
        P = np.c_[gx.ravel(), gy.ravel()]
        X = api.warp_chain_points(layers, P)
        wrong, _ = _invert_chain(list(reversed(layers)), X)
        self.assertGreater(np.hypot(*(wrong - P).T).max(), 1.0,
                           "reversing the inverse changed nothing; these layers "
                           "commute, so the round-trip test is vacuous")

    def test_one_layer_is_exactly_the_historical_single_lattice_path(self):
        d = self.lay(self.cap * 0.9)["dst"]
        M = api.affine_from_params(0, 0, 0, 0, 0, 1.0, 1.0)
        A_old, _, rid_old, _ = api.warp_composite(self.b, M, self.src, d, self.R)
        A_new, _, rid_new, _ = api.warp_composite_layers(
            self.b, M, [{"src": self.src, "dst": d, "R": self.R}])
        self.assertTrue(np.array_equal(A_old, A_new))
        self.assertTrue(np.array_equal(rid_old, rid_new))

    def test_an_empty_stack_is_the_pure_affine(self):
        M = api.affine_from_params(0, 0, 0, 0, 0, 1.0, 1.0)
        A_aff, _, rid_aff = api.warp_affine(self.b, M)
        A_chain, _, rid_chain, inv = api.warp_composite_layers(self.b, M, [])
        self.assertTrue(np.array_equal(A_aff, A_chain))
        self.assertTrue(np.array_equal(rid_aff, rid_chain))
        self.assertEqual(inv["n_bad"], 0)

    def test_determinants_multiply_so_the_stack_needs_its_own_gate(self):
        """The reason a per-layer verdict is not enough. Each of these is legal;
        eight of them are not."""
        one = [self.lay(self.cap * 0.999, _cap_axis(self.ext))]
        v = api.validate_warp_grid(self.src, one[0]["dst"], self.R, self.ext, 13, 13)
        self.assertTrue(v["ok"], "the single layer should be legal")
        mn1, _ = api.warp_composed_jac(one, self.ext)
        mn8, _ = api.warp_composed_jac(one * 8, self.ext)
        self.assertLess(mn8, mn1, "stacking did not reduce the determinant")
        self.assertLessEqual(mn8, api.WARP_COMPOSED_JAC_MIN,
                             "eight maxed-out layers should trip the composed floor")


@unittest.skipUnless(_B is not None, "no registration bundle at the configured resolution; see tests/_bundles.py")
class JobValidationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from _bundles import first_bundle
        cls.path = first_bundle()
        cls.b = _B
        cls.ext = list(api.warp_grid_extent(cls.b))
        cls.src = np.asarray(api.warp_grid_nodes(cls.b), float)
        cls.R = api.warp_grid_radius(cls.b)
        kinds = np.asarray(api.warp_node_kinds(13, 13))
        cls.node = int(np.where(kinds == api.WARP_NODE_FREE)[0][60])
        cls.cap = api.warp_max_disp(cls.ext, 13, 13, None)

    def lay(self, amt, axis=0):
        d = self.src.copy()
        d[self.node, axis] += amt
        return {"algo": api.WARP_GRID_ALGO, "rows": 13, "cols": 13, "R": self.R,
                "src": self.src.tolist(), "dst": d.tolist()}

    def test_a_single_lattice_is_not_rewritten_into_a_stack(self):
        """Most saved curations are one lattice. Reshaping them on save would
        rewrite history for no gain, and break any reader expecting the old
        shape."""
        from web.jobs import _clean_warp_chain
        out = _clean_warp_chain(self.lay(self.cap * 0.5))
        self.assertNotIn("layers", out)
        self.assertIn("src", out)

    def test_a_stack_is_stored_unambiguously(self):
        from web.jobs import _clean_warp_chain
        out = _clean_warp_chain({"layers": [self.lay(self.cap * 0.4),
                                            self.lay(self.cap * 0.4, 1)]})
        self.assertEqual(len(out["layers"]), 2)

    def test_the_layer_cap_is_enforced_before_anything_is_written(self):
        from web.jobs import _clean_warp_chain
        with self.assertRaises(ValueError) as cm:
            _clean_warp_chain({"layers": [self.lay(1.0)] * (api.WARP_MAX_LAYERS + 1)})
        self.assertIn(str(api.WARP_MAX_LAYERS), str(cm.exception))

    def test_a_rejected_layer_is_named(self):
        """With a stack, "the warp is invalid" is not actionable on its own."""
        from web.jobs import _assert_warp_valid
        bad = self.lay(self.cap * 8)
        with self.assertRaises(ValueError) as cm:
            _assert_warp_valid({"layers": [self.lay(self.cap * 0.3), bad]}, self.path)
        self.assertIn("layer 2 of 2", str(cm.exception))

    def test_a_legal_stack_passes(self):
        from web.jobs import _assert_warp_valid
        _assert_warp_valid({"layers": [self.lay(self.cap * 0.4),
                                       self.lay(self.cap * 0.4, 1)]}, self.path)

    def test_the_composed_floor_is_enforced_at_the_api_edge(self):
        """Not only in finalize: a stack that folds should be a 400 with a
        reason, not a job that dies in the worker after the user confirmed."""
        from web.jobs import _assert_warp_valid
        with self.assertRaises(ValueError) as cm:
            _assert_warp_valid({"layers": [self.lay(self.cap * 0.999,
                                                    _cap_axis(self.ext))] * 8},
                               self.path)
        self.assertIn("compose", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
