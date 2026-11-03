"""A region's declared plane reaches step 2 (2026-10-23).

jobs.create seeds orientation.plane from the region (sagittal sections open step
2 on Sagittal), but from 2026-09-21 a leftover second "orientation": None key in
the same dict literal silently overrode it, so every job opened on coronal.
Users corrected the plane by hand, which is why no job came out wrong; the
positive-control scorer passes the plane explicitly. These tests pin the seeding
and guard against duplicate keys in any dict literal under web/.
"""
import ast
import glob
import os
import sys
import tempfile
import unittest
from unittest import mock

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from web import jobs               # noqa: E402
import pipeline_api                 # noqa: E402


class SeededPlaneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = jobs.JOBS_DIR
        jobs.JOBS_DIR = self._tmp.name
        self._ok = mock.patch.object(pipeline_api, "validate_region", return_value=(True, "ok"))
        self._ok.start()

    def tearDown(self):
        self._ok.stop()
        jobs.JOBS_DIR = self._old
        self._tmp.cleanup()

    def test_a_sagittal_region_opens_on_sagittal(self):
        j = jobs.create(self._tmp.name, "sag_x", plane="sagittal")
        self.assertEqual(j["orientation"]["plane"], "sagittal")
        self.assertEqual(jobs.load(j["id"])["orientation"]["plane"], "sagittal")

    def test_no_declared_plane_leaves_orientation_unset(self):
        self.assertIsNone(jobs.create(self._tmp.name, "x")["orientation"])

    def test_confirming_step_2_keeps_the_seeded_plane(self):
        j = jobs.create(self._tmp.name, "sag_x", plane="sagittal")
        j = jobs.set_orientation(j["id"], 0.0, "none")
        self.assertEqual(j["state"], "ORIENTATION_CONFIRMED")
        self.assertEqual(j["orientation"]["plane"], "sagittal")


class NoDuplicateKeysTests(unittest.TestCase):
    def test_no_dict_literal_under_web_repeats_a_key(self):
        bad = []
        for path in glob.glob(os.path.join(_STUDY, "web", "*.py")):
            for node in ast.walk(ast.parse(open(path).read())):
                if isinstance(node, ast.Dict):
                    keys = [k.value for k in node.keys
                            if isinstance(k, ast.Constant)]
                    for k in {k for k in keys if keys.count(k) > 1}:
                        bad.append(f"{os.path.basename(path)}:{node.lineno} {k!r}")
        self.assertEqual(bad, [], "a repeated key silently drops the earlier value")


if __name__ == "__main__":
    unittest.main(verbosity=2)
