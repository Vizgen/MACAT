"""Each job writes its artifacts into its own folder (2026-10-28).

Until then every job wrote flat into the output root under a per-SECTION name,
so two jobs on one section overwrote each other's bundle, fit and per-cell CSV:
job 20260925_145018 (registered at -1 deg) ended up holding its sibling's 0 deg
fit. A new job now gets out_dir = jobs/<id>/outputs/. These tests pin that two
jobs on one section cannot collide, that deleting one leaves the other's files
alone, and that the server still finds and serves per-job files.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

# an isolated store, set before the app is imported (it reads it at import)
_TMP = tempfile.TemporaryDirectory()
os.environ["ALLEN_OUT_ROOT"] = _TMP.name

import pipeline_api                 # noqa: E402
from web import jobs                # noqa: E402


def _touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


class PerJobOutputTests(unittest.TestCase):
    def setUp(self):
        self.exp = tempfile.mkdtemp(dir=_TMP.name, prefix="exp_")
        self._ok = mock.patch.object(pipeline_api, "validate_region",
                                     return_value=(True, "ok"))
        self._ok.start()
        self.a = jobs.create(self.exp, "sec_R1")
        self.b = jobs.create(self.exp, "sec_R1")

    def tearDown(self):
        self._ok.stop()

    def test_two_jobs_on_one_section_get_separate_folders(self):
        self.assertEqual(self.a["tag"], self.b["tag"], "same section, same file names")
        self.assertNotEqual(self.a["out_dir"], self.b["out_dir"])
        for j in (self.a, self.b):
            self.assertEqual(j["out_dir"], jobs.job_out_dir(j["id"]))
            self.assertTrue(j["out_dir"].startswith(jobs.job_dir(j["id"]) + os.sep))

    def test_one_job_cannot_overwrite_the_others_files(self):
        name = f"cell_metadata_with_allen_regions{self.a['tag']}.csv"
        pa = _touch(os.path.join(self.a["out_dir"], name), "job a")
        pb = _touch(os.path.join(self.b["out_dir"], name), "job b")
        self.assertNotEqual(pa, pb)
        self.assertEqual(open(pa).read(), "job a")

    def test_deleting_one_job_leaves_the_others_files(self):
        name = f"manual_bundle{self.a['tag']}.npz"
        pa = _touch(os.path.join(self.a["out_dir"], name))
        pb = _touch(os.path.join(self.b["out_dir"], name))
        for j, p in ((self.a, pa), (self.b, pb)):
            j = jobs.load(j["id"])
            j["register"] = {"status": "done", "result": {"bundle_path": p}}
            jobs.save(j)
        jobs.delete_jobs([self.a["id"]])
        self.assertFalse(os.path.exists(pa))
        self.assertFalse(os.path.exists(jobs.job_dir(self.a["id"])))
        self.assertTrue(os.path.exists(pb))


class ServerFindsPerJobFilesTests(unittest.TestCase):
    def test_bundles_in_job_folders_are_discovered_and_downloadable(self):
        from web import server
        exp = tempfile.mkdtemp(dir=_TMP.name, prefix="exp_")
        with mock.patch.object(pipeline_api, "validate_region", return_value=(True, "ok")):
            j = jobs.create(exp, "sec_R2")
        p = _touch(os.path.join(j["out_dir"], f"manual_bundle{j['tag']}.npz"))
        self.assertIn(p, server._find_bundles())
        resp = server.download(p, job=j["id"])
        self.assertEqual(os.path.abspath(resp.path), os.path.abspath(p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
