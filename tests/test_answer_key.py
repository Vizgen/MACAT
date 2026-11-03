"""expected.json: known answers that travel with the data (v0.5.0).

A region folder may carry its answer key. MACAT then shows the known level in
step 1's "Your sections", pre-fills step 2 (plane, half, rotation, flip) and
opens step 3 on that level -- wherever the folder lives, local or S3. The
format is documented in web/answer_key.py and docs/allen_annotated_data.md.
"""
import json
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
os.environ["ALLEN_OUT_ROOT"] = os.path.join(_TMP.name, "store")
os.environ.pop("MACAT_DATA_DIR", None)

from web import answer_key, datafolder, jobs, s3source    # noqa: E402

HEMI = {"name": "pc_coronal_hemi_x", "plane": "coronal", "hemisection": "left",
        "orientation_angle": 4.0, "orientation_flip": "none",
        "idx_25um": 285, "idx_25um_tolerance": 4, "idx_50um": 142, "idx_50um_tolerance": 2}
SAG = {"plane": "sagittal", "hemisection": "", "orientation_angle": 353.0,
       "orientation_flip": "y", "idx_25um": 197, "idx_25um_tolerance": 3,
       "idx_25um_mirror": 258}


def _region(root, exp, reg, answer=None):
    d = os.path.join(root, exp, reg)
    os.makedirs(d, exist_ok=True)
    for f in ("cell_metadata.csv", "cell_by_gene.csv"):
        open(os.path.join(d, f), "w").close()
    if answer is not None:
        with open(os.path.join(d, "expected.json"), "w") as f:
            json.dump(answer, f)
    return d


class ReadTests(unittest.TestCase):
    def test_level_at_the_servers_resolution(self):
        self.assertEqual(answer_key.known_level(HEMI, 25),
                         {"idx": 285, "tol": 4, "mirror": None, "res_um": 25})
        self.assertEqual(answer_key.known_level(HEMI, 50)["idx"], 142)
        self.assertEqual(answer_key.known_level(SAG, 25)["mirror"], 258)
        self.assertIsNone(answer_key.known_level(SAG, 50), "no 50 um level given")

    def test_orientation_pre_fill(self):
        self.assertEqual(answer_key.orientation(HEMI),
                         {"plane": "coronal", "hemisection": "left", "angle": 4.0, "flip": "none"})
        o = answer_key.orientation(SAG)
        self.assertEqual((o["plane"], o["flip"], "hemisection" in o), ("sagittal", "y", False))

    def test_missing_or_broken_files_are_none(self):
        d = tempfile.mkdtemp(dir=_TMP.name)
        self.assertIsNone(answer_key.read(d))
        with open(os.path.join(d, "expected.json"), "w") as f:
            f.write("{not json")
        self.assertIsNone(answer_key.read(d))
        self.assertIsNone(answer_key.orientation({"plane": "oblique"}))


class DataFolderTests(unittest.TestCase):
    def test_sections_show_their_known_level(self):
        root = tempfile.mkdtemp(dir=_TMP.name)
        _region(root, "pc_hemi", "region_R1", HEMI)
        _region(root, "mine", "region_R1")
        secs, _ = datafolder.scan(root)
        by = {s["experiment"]: s for s in secs}
        self.assertEqual(by["pc_hemi"]["known_level"]["idx"], 285)
        self.assertEqual(by["pc_hemi"]["hemisection"], "left")
        self.assertNotIn("known_level", by["mine"])

    def test_s3_sections_read_the_key_from_the_bucket(self):
        keys = {"pc_sag/region_R1/cell_metadata.csv": {}, "pc_sag/region_R1/cell_by_gene.csv": {},
                "pc_sag/region_R1/expected.json": {}}
        with mock.patch.object(s3source, "list_objects", return_value=keys), \
             mock.patch.object(s3source, "_aws", return_value=json.dumps(SAG)) as aws:
            secs, _ = datafolder.scan("s3://bucket/prefix/")
        self.assertEqual(secs[0]["known_level"]["idx"], 197)
        self.assertIn("s3://bucket/prefix/pc_sag/region_R1/expected.json", aws.call_args[0])

    def test_s3_jobs_fetch_the_key_with_the_cell_tables(self):
        self.assertIn("expected.json", s3source.SMALL)


class JobTests(unittest.TestCase):
    def setUp(self):
        from web import server
        self.server = server
        self._ok = mock.patch("pipeline_api.validate_region", return_value=(True, "ok"))
        self._ok.start()

    def tearDown(self):
        self._ok.stop()

    def test_a_section_with_a_key_pre_fills_step_2_and_shows_its_level(self):
        d = _region(tempfile.mkdtemp(dir=_TMP.name), "pc_hemi", "region_R1", HEMI)
        job = self.server.create_job(self.server.JobCreateReq(exp_path=d, name="pc_hemi_region_R1"))
        o = job["orientation"]
        self.assertEqual((o["plane"], o["hemisection"], o["angle"], o["flip"]),
                         ("coronal", "left", 4.0, "none"))
        self.assertEqual(job["state"], "INPUT_READY", "pre-filled, not confirmed")
        view = self.server._with_atlas_check(jobs.load(job["id"]))
        self.assertEqual(view["known_level"]["idx"], 285)
        self.assertTrue(view["answer_orientation"])
        self.assertEqual(view["name"], "pc_hemi_region_R1")

    def test_a_section_without_a_key_starts_as_before(self):
        d = _region(tempfile.mkdtemp(dir=_TMP.name), "mine", "region_R1")
        job = self.server.create_job(self.server.JobCreateReq(exp_path=d))
        self.assertIsNone(job["orientation"])
        view = self.server._with_atlas_check(jobs.load(job["id"]))
        self.assertNotIn("known_level", view)
        self.assertNotIn("answer_orientation", view)

    def test_confirming_step_2_keeps_the_pre_filled_half(self):
        d = _region(tempfile.mkdtemp(dir=_TMP.name), "pc_hemi", "region_R1", HEMI)
        job = self.server.create_job(self.server.JobCreateReq(exp_path=d))
        o = job["orientation"]
        j = jobs.set_orientation(job["id"], o["angle"], o["flip"], o["plane"], o["hemisection"])
        self.assertEqual((j["state"], j["orientation"]["hemisection"]),
                         ("ORIENTATION_CONFIRMED", "left"))


class NoExampleListTests(unittest.TestCase):
    def test_the_example_list_endpoint_is_gone(self):
        from web import server
        paths = {getattr(r, "path", "") for r in server.app.routes}
        self.assertNotIn("/api/regions", paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
