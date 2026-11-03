"""The user's data folder: step 1's "Your sections" (web/datafolder.py).

Rules agreed 2026-10-28:
  * sections are exactly <data folder>/<experiment>/region_*, as MERSCOPE writes
    them, with both cell tables; nothing deeper is searched;
  * the folder is saved and survives a restart; MACAT_DATA_DIR overrides it;
  * it is scanned when set and on Rescan, never on page load;
  * only the machine MACAT runs on may view or change it.
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

_TMP = tempfile.TemporaryDirectory()
_STORE = os.path.join(_TMP.name, "store")
os.environ["ALLEN_OUT_ROOT"] = _STORE                            # before the app
os.environ.pop("MACAT_DATA_DIR", None)

from fastapi import HTTPException          # noqa: E402
from web import datafolder, jobs, s3source  # noqa: E402

# Under `discover`, another test may import web.jobs first, fixing OUT_ROOT
# before the line above runs; settings.json would then land in that root,
# possibly the real pipeline_output/. Pin it to this file's store.
_OUT_ROOT = mock.patch.object(jobs, "OUT_ROOT", _STORE)


def setUpModule():
    _OUT_ROOT.start()


def tearDownModule():
    _OUT_ROOT.stop()


def _touch(*parts):
    p = os.path.join(*parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()


def _region(root, exp, reg, tables=True, dapi=False):
    if tables:
        _touch(root, exp, reg, "cell_metadata.csv")
        _touch(root, exp, reg, "cell_by_gene.csv")
    else:
        _touch(root, exp, reg, "cell_metadata.csv")
    if dapi:
        _touch(root, exp, reg, "images", "mosaic_DAPI_z3.tif")
        _touch(root, exp, reg, "images", "micron_to_mosaic_pixel_transform.csv")


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=_TMP.name)
        _region(self.root, "exp1", "region_R1", dapi=True)
        _region(self.root, "exp1", "region_R2", tables=False)   # no cell_by_gene
        _region(self.root, "exp2", "region_S1")
        _region(self.root, "exp2", "not_a_region")
        _region(self.root, "deep", os.path.join("exp3", "region_R1"))  # too deep
        _region(self.root, ".hidden", "region_R1")

    def test_exactly_experiment_slash_region_with_both_tables(self):
        secs, truncated = datafolder.scan(self.root)
        self.assertEqual([(s["experiment"], s["region"]) for s in secs],
                         [("exp1", "region_R1"), ("exp2", "region_S1")])
        self.assertFalse(truncated)
        self.assertEqual([s["dapi"] for s in secs], [True, False])
        self.assertEqual(secs[0]["name"], "exp1_region_R1")
        self.assertEqual(secs[0]["path"], os.path.join(self.root, "exp1", "region_R1"))

    def test_a_missing_folder_is_an_error(self):
        with self.assertRaises(ValueError):
            datafolder.scan(os.path.join(self.root, "nope"))

    def test_s3_prefix_from_one_listing(self):
        keys = {"exp1/region_R1/cell_metadata.csv": {}, "exp1/region_R1/cell_by_gene.csv": {},
                "exp1/region_R1/images/mosaic_DAPI_z3.tif": {},
                "exp1/region_R1/images/micron_to_mosaic_pixel_transform.csv": {},
                "exp1/region_R2/cell_metadata.csv": {},              # incomplete
                "flat_section/cell_metadata.csv": {},                 # not exp/region
                "flat_section/cell_by_gene.csv": {}, "README.txt": {}}
        with mock.patch.object(s3source, "list_objects", return_value=keys) as lo:
            secs, _ = datafolder.scan("s3://bucket/prefix/")
        lo.assert_called_once()
        self.assertEqual([(s["experiment"], s["region"], s["dapi"], s["path"]) for s in secs],
                         [("exp1", "region_R1", True, "s3://bucket/prefix/exp1/region_R1/")])


class SettingTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=_TMP.name)
        _region(self.root, "exp1", "region_R1")
        os.environ.pop(datafolder.ENV, None)

    def tearDown(self):
        os.environ.pop(datafolder.ENV, None)

    def test_set_is_saved_and_not_rescanned_on_load(self):
        cur = datafolder.set_folder(self.root)
        self.assertEqual(len(cur["sections"]), 1)
        self.assertTrue(os.path.exists(os.path.join(_STORE, "settings.json")))
        _region(self.root, "exp2", "region_R1")                    # appears later
        self.assertEqual(len(datafolder.current()["sections"]), 1, "page load must not rescan")
        self.assertEqual(len(datafolder.rescan()["sections"]), 2)

    def test_a_bad_folder_is_saved_with_its_error(self):
        cur = datafolder.set_folder(os.path.join(self.root, "missing"))
        self.assertIn("not a folder", cur["error"])
        self.assertEqual(cur["sections"], [])

    def test_clearing(self):
        datafolder.set_folder(self.root)
        self.assertIsNone(datafolder.clear()["folder"])

    def test_the_environment_overrides_and_locks(self):
        datafolder.set_folder(self.root)
        other = tempfile.mkdtemp(dir=_TMP.name)
        _region(other, "expX", "region_R9")
        os.environ[datafolder.ENV] = other
        cur = datafolder.current()
        self.assertTrue(cur["locked"])
        self.assertEqual([s["experiment"] for s in cur["sections"]], ["expX"])
        with self.assertRaises(PermissionError):
            datafolder.set_folder(self.root)


class LocalOnlyTests(unittest.TestCase):
    def setUp(self):
        from web import server
        self.server = server
        os.environ.pop(datafolder.ENV, None)

    def _req(self, host):
        return SimpleNamespace(client=SimpleNamespace(host=host))

    def test_only_the_machine_macat_runs_on_may_see_or_change_it(self):
        self.assertTrue(self.server.data_folder(self._req("127.0.0.1"))["available"])
        self.assertTrue(self.server.data_folder(self._req("::1"))["available"])
        self.assertFalse(self.server.data_folder(self._req("192.0.2.10"))["available"])
        with self.assertRaises(HTTPException) as cm:
            self.server.set_data_folder(self.server.DataFolderReq(folder="/"),
                                        self._req("192.168.1.20"))
        self.assertEqual(cm.exception.status_code, 403)

    def test_a_picked_section_creates_a_job(self):
        root = tempfile.mkdtemp(dir=_TMP.name)
        _region(root, "exp1", "region_R1")
        cur = self.server.set_data_folder(self.server.DataFolderReq(folder=root),
                                          self._req("127.0.0.1"))
        sec = cur["sections"][0]
        with mock.patch("pipeline_api.validate_region", return_value=(True, "ok")):
            job = self.server.create_job(self.server.JobCreateReq(exp_path=sec["path"],
                                                                  name=sec["name"]))
        self.assertEqual(job["name"], "exp1_region_R1")
        self.assertEqual(job["exp_path"], sec["path"])

    def test_quoted_paths_work_in_both_boxes(self):
        """Windows' "Copy as path" wraps a path in double quotes (found in user
        testing, 2026-11-03): the data folder and the experiment folder must
        both accept it."""
        root = tempfile.mkdtemp(dir=_TMP.name)
        _region(root, "exp1", "region_R1")
        cur = self.server.set_data_folder(self.server.DataFolderReq(folder=f' "{root}" '),
                                          self._req("127.0.0.1"))
        self.assertEqual(cur["folder"], root)
        self.assertEqual(len(cur["sections"]), 1)
        sec = cur["sections"][0]
        with mock.patch("pipeline_api.validate_region", return_value=(True, "ok")):
            job = self.server.create_job(self.server.JobCreateReq(exp_path=f'"{sec["path"]}"'))
        self.assertEqual(job["exp_path"], sec["path"])


class CleanPathTests(unittest.TestCase):
    def test_one_pair_of_surrounding_quotes_is_removed(self):
        self.assertEqual(datafolder.clean_path('"/a/b c"'), "/a/b c")
        self.assertEqual(datafolder.clean_path("  '/a/b'  "), "/a/b")
        self.assertEqual(datafolder.clean_path('"C:\\Data\\MERFISH"'), "C:\\Data\\MERFISH")
        self.assertEqual(datafolder.normalize('"s3://bucket/prefix"'), "s3://bucket/prefix/")

    def test_other_quotes_are_left_alone(self):
        self.assertEqual(datafolder.clean_path('/a/"b"/c'), '/a/"b"/c')     # inside a name
        self.assertEqual(datafolder.clean_path('"/a/b'), '"/a/b')           # unmatched
        self.assertEqual(datafolder.clean_path('"/a/b\''), '"/a/b\'')       # mismatched
        self.assertEqual(datafolder.clean_path('""'), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
