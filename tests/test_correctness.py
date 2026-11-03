"""Correctness regression tests for two confirmed bugs, found in an internal
correctness audit:

  Bug 1 — AP consistency: changing AP (or orientation) after registration left a
          stale bundle (built at the old AP) driving curation/finalize while the
          UI reported the new AP.  Cells were silently labelled at the wrong AP.

  Bug 2 — stale Results after re-editing: in-memory server caches were keyed by
          file *path*, so a re-run registration / re-finalized CSV that
          overwrote the same path kept serving the old cached result.

These tests exercise the backend guarantees added by the fix:
  * jobs.set_ap / set_orientation invalidate downstream state (Test 1, 2)
  * server._load / QC + region-export cache keys refresh on content change
    (Test 3, 4) via the (path, fingerprint) key
  * jobs.assert_ap_consistent / confirm_curation refuse a stale bundle (Test 5)
  * confirm_curation is re-entrant from COMPLETED so re-editing re-finalizes
    instead of showing a stale Results (Test 6, backend half; the front-end
    navigation guard lives in App.jsx / CurationStep.jsx and is covered by the
    vite build).

Written with stdlib unittest (no external deps — the analysis env has no
pytest). Run:

  OMP_NUM_THREADS=1 python -m unittest tests.test_correctness -v
"""
import os
import sys
import time
import tempfile
import unittest

# Put conda's libstdc++ (GLIBCXX_3.4.29, needed by shapely/libgeos) ahead of the
# system one, and make the study dir importable — same shim as the
# validation scripts, so this module also runs under a plain interpreter.
_pre = os.path.join(sys.prefix, "lib", "libstdc++.so.6")
if os.path.exists(_pre) and _pre not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = _pre + (
        ":" + os.environ["LD_PRELOAD"] if os.environ.get("LD_PRELOAD") else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import numpy as np                       # noqa: E402
import config                            # noqa: E402
from web import jobs                     # noqa: E402


# --------------------------------------------------------------------- helpers
def _fake_bundle(path, ap):
    """Minimal audited registration bundle used by state-machine tests."""
    np.savez(
        path,
        ap=int(ap),
        sampled_ap_idx=int(ap),
        sampled_ap_um=float(ap),
        # a bundle registered by THIS configuration, not a legacy 50 um one
        dx=float(config.PLANE_DX_UM),
    )


def _bump_mtime(path):
    """Force a strictly newer mtime so the (mtime_ns, size) fingerprint changes
    even when a rewrite keeps the same size and lands in the same clock tick."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._orig_jobs_dir = jobs.JOBS_DIR
        jobs.JOBS_DIR = os.path.join(self.tmp, "jobs")
        os.makedirs(jobs.JOBS_DIR, exist_ok=True)

    def tearDown(self):
        jobs.JOBS_DIR = self._orig_jobs_dir
        self._tmp.cleanup()

    def _make_job(self, state, ap_idx, bundle_ap=None, orientation=None):
        jid = "testjob"
        job = {
            "id": jid, "state": state,
            "created": time.time(), "updated": time.time(),
            "exp_path": os.path.join(self.tmp, "exp"),
            "region_key": None, "name": "test", "tag": "_test",
            "out_dir": os.path.join(self.tmp, "out"),
            "orientation": orientation or {"angle": 0.0, "flip": "none"},
            "ap_idx": ap_idx,
            # picked on the atlas this configuration runs, like a fresh job
            "ap_res_um": float(config.PLANE_DX_UM),
            "register": {"status": "idle"},
            "manual": None, "error": None,
        }
        if bundle_ap is not None:
            bp = os.path.join(self.tmp, "manual_bundle_test.npz")
            _fake_bundle(bp, bundle_ap)
            job["register"] = {"status": "done", "result": {"bundle_path": bp}}
            job["manual"] = {"status": "done", "result": {"foo": "old"}}
        jobs.save(job)
        return jid


# ------------------------------------------------------------------- Test 1
class TestApInvalidation(_Base):
    def test_ap_change_invalidates_registration(self):
        """Changing AP after registration rolls back to AP_CONFIRMED and drops
        the stale registration + curation."""
        jid = self._make_job("AUTO_REGISTRATION_DONE", ap_idx=150, bundle_ap=150)
        out = jobs.set_ap(jid, 154)
        self.assertEqual(out["ap_idx"], 154)
        self.assertEqual(out["state"], "AP_CONFIRMED")
        self.assertEqual(out["register"], {"status": "idle"})
        self.assertIsNone(out["manual"])

    def test_ap_unchanged_keeps_registration(self):
        """Re-confirming the SAME AP must not throw away a good registration."""
        jid = self._make_job("AUTO_REGISTRATION_DONE", ap_idx=150, bundle_ap=150)
        out = jobs.set_ap(jid, 150)
        self.assertEqual(out["state"], "AUTO_REGISTRATION_DONE")
        self.assertEqual(out["register"]["status"], "done")
        self.assertIsNotNone(out["manual"])


# ------------------------------------------------------------------- Test 2
class TestOrientationInvalidation(_Base):
    def test_orientation_change_invalidates_downstream(self):
        """Changing orientation after completion clears registration + curation
        and rolls back to ORIENTATION_CONFIRMED (AP number kept as default)."""
        jid = self._make_job("COMPLETED", ap_idx=150, bundle_ap=150,
                             orientation={"angle": 0.0, "flip": "none"})
        out = jobs.set_orientation(jid, 12.0, "none")
        # `plane` joined the orientation on 2026-09-21 and defaults to coronal;
        # `hemisection` joined on 2026-10-20 and defaults to None (whole section)
        self.assertEqual(out["orientation"],
                         {"angle": 12.0, "flip": "none", "plane": "coronal",
                          "hemisection": None})
        self.assertEqual(out["ap_idx"], 150)
        self.assertEqual(out["state"], "ORIENTATION_CONFIRMED")
        self.assertEqual(out["register"], {"status": "idle"})
        self.assertIsNone(out["manual"])

    def test_orientation_unchanged_is_noop(self):
        jid = self._make_job("COMPLETED", ap_idx=150, bundle_ap=150,
                             orientation={"angle": 5.0, "flip": "x"})
        out = jobs.set_orientation(jid, 5.0, "x")
        self.assertEqual(out["state"], "COMPLETED")
        self.assertEqual(out["register"]["status"], "done")


# ------------------------------------------------------------------- Test 3
class TestBundleCache(unittest.TestCase):
    def test_bundle_cache_refreshes_on_content_change(self):
        """server._load must re-read a bundle overwritten in place (same path,
        new content) without a restart — proven via the (mtime, size)
        fingerprint. A fake load_bundle keeps the test light."""
        from web import server
        with tempfile.TemporaryDirectory() as td:
            bp = os.path.join(td, "manual_bundle_test.npz")
            _fake_bundle(bp, 150)

            orig_load = server.api.load_bundle
            orig_cache = server._bundles
            server.api.load_bundle = lambda p: {"ap": jobs.bundle_ap(p)}
            server._bundles = {}
            try:
                self.assertEqual(server._load(bp)["ap"], 150)
                _fake_bundle(bp, 154)       # overwrite in place
                _bump_mtime(bp)
                self.assertEqual(server._load(bp)["ap"], 154,
                                 "stale bundle served after in-place overwrite")
            finally:
                server.api.load_bundle = orig_load
                server._bundles = orig_cache


# ------------------------------------------------------------------- Test 4
class TestQcExportCacheKeys(unittest.TestCase):
    def test_qc_and_export_cache_keys_track_content(self):
        """The QC and region-export caches are keyed by (csv_path, fingerprint);
        a re-finalized CSV at the same path yields a NEW key, so the previous
        cached result can never be served."""
        from web import server
        with tempfile.TemporaryDirectory() as td:
            csv = os.path.join(td, "mapped.csv")
            with open(csv, "w") as f:
                f.write("a,b\n1,2\n")

            key1 = (csv, server._fp(csv))
            server._qc_cache[key1] = "OLD_QC"
            server._region_exports_cache[key1] = "OLD_EXPORTS"
            try:
                with open(csv, "w") as f:       # re-finalize overwrites same path
                    f.write("a,b\n3,4\n5,6\n")
                _bump_mtime(csv)

                key2 = (csv, server._fp(csv))
                self.assertNotEqual(key2, key1)
                self.assertNotIn(key2, server._qc_cache)
                self.assertNotIn(key2, server._region_exports_cache)
            finally:
                server._qc_cache.pop(key1, None)
                server._region_exports_cache.pop(key1, None)


# ------------------------------------------------------------------- Test 5
class TestApMismatchGuard(_Base):
    def test_ap_mismatch_guard_raises(self):
        """assert_ap_consistent raises when the bundle AP disagrees with
        job.ap_idx, and confirm_curation refuses to finalize a stale bundle."""
        jid = self._make_job("AUTO_REGISTRATION_DONE", ap_idx=154, bundle_ap=150)
        job = jobs.load(jid)
        with self.assertRaisesRegex(ValueError, "AP mismatch"):
            jobs.assert_ap_consistent(job)
        with self.assertRaisesRegex(ValueError, "AP mismatch"):
            jobs.confirm_curation(jid, {"cx": 0.0, "cy": 0.0})

    def test_ap_consistent_passes_when_matching(self):
        jid = self._make_job("AUTO_REGISTRATION_DONE", ap_idx=150, bundle_ap=150)
        job = jobs.load(jid)
        self.assertIsNone(jobs.assert_ap_consistent(job))


# ------------------------------------------------------------------- Test 6
class TestReentrantFinalize(_Base):
    def test_confirm_curation_reentrant_from_completed(self):
        """Re-editing a COMPLETED job re-runs finalize (fresh result) instead of
        leaving a stale Results — backend half of the dirty-Results guard. The
        heavy CSRBF worker is stubbed so only the state machine is exercised."""
        jid = self._make_job("COMPLETED", ap_idx=150, bundle_ap=150)
        orig = jobs._finalize_worker
        jobs._finalize_worker = lambda *a, **k: {"foo": "fresh"}
        try:
            out = jobs.confirm_curation(
                jid, {"cx": 0.0, "cy": 0.0, "tx": 0.0, "ty": 0.0,
                      "rot_deg": 0.0, "sx": 1.0, "sy": 1.2})
        finally:
            jobs._finalize_worker = orig
        self.assertEqual(out["state"], "COMPLETED")
        self.assertEqual(out["manual"]["status"], "done")
        self.assertEqual(out["manual"]["result"], {"foo": "fresh"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
