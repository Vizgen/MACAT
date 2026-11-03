"""report.zip's manifest says which atlas the fit used (found in user
testing, 2026-11-03).

It was always null: register_region returns the atlas provenance, but the
registration worker copies a whitelist of keys into job.json and the
provenance was not on it. New jobs now keep it; older jobs have it in their
registration bundle, which has always stored it.
"""
import os
import sys
import tempfile
import unittest

import numpy as np

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("ALLEN_OUT_ROOT", os.path.join(_TMP.name, "store"))

from web import server        # noqa: E402

PROV = {"atlas_id": "allen_mouse_25um", "dataset_version": "1.2",
        "resolution_um": [25.0, 25.0, 25.0]}


class ManifestAtlasTests(unittest.TestCase):
    def test_from_job_json(self):
        self.assertEqual(server._atlas_provenance({}, {"atlas_provenance": PROV}), PROV)
        self.assertEqual(server._atlas_provenance({"atlas_provenance": PROV}, {}), PROV)

    def test_an_older_job_reads_it_from_its_bundle(self):
        path = os.path.join(_TMP.name, "manual_bundle_old.npz")
        np.savez(path, atlas_provenance=np.array(PROV, dtype=object), xJ0=np.zeros(2))
        self.assertEqual(server._atlas_provenance({}, {"bundle_path": path}), PROV)

    def test_none_when_nothing_records_it(self):
        path = os.path.join(_TMP.name, "manual_bundle_bare.npz")
        np.savez(path, xJ0=np.zeros(2))
        self.assertIsNone(server._atlas_provenance({}, {"bundle_path": path}))
        self.assertIsNone(server._atlas_provenance({}, {"bundle_path": "/nonexistent.npz"}))
        self.assertIsNone(server._atlas_provenance({}, {}))

    def test_the_worker_keeps_it_in_job_json(self):
        with open(os.path.join(_STUDY, "web", "_register_worker.py")) as fh:
            src = fh.read()
        whitelist = src[src.index('job["register"]["result"] = {'):src.index('job["state"] = "AUTO_REGISTRATION_DONE"')]
        self.assertIn('"atlas_provenance"', whitelist)


if __name__ == "__main__":
    unittest.main()
