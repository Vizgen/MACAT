import os
import tempfile
import unittest

import numpy as np

from web import jobs


def _bundle(path, *, declared, sampled_idx, sampled_um):
    np.savez_compressed(
        path,
        ap=np.array(declared),
        sampled_ap_idx=np.array(sampled_idx),
        sampled_ap_um=np.array(sampled_um),
        tf=np.full((3, 4, 3), sampled_um, dtype=np.float32),
    )


class ApIntegrityTests(unittest.TestCase):
    def test_bundle_ap_audit_reports_declared_and_sampled_slice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bundle.npz")
            _bundle(path, declared=155, sampled_idx=155, sampled_um=1175.0)
            self.assertEqual(
                jobs.bundle_ap_audit(path), (155, 155, 1175.0))

    def test_ap_guard_rejects_metadata_content_false_agreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bundle.npz")
            _bundle(path, declared=155, sampled_idx=108, sampled_um=-1175.0)
            job = {
                "id": "mirrored",
                "ap_idx": 155,
                "register": {"result": {"bundle_path": str(path)}},
            }
            with self.assertRaisesRegex(
                    ValueError, "sampled from a different slice"):
                jobs.assert_ap_consistent(job)


if __name__ == "__main__":
    unittest.main(verbosity=2)
