"""web/s3source.py: experiments read from S3 through a local cache.

The AWS CLI is simulated: these tests pin the RULES (what is fetched when, what
is refused, what is never requested), not the network. The end-to-end run
against a real bucket was done by hand on 2026-10-20.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from web import s3source as s3                        # noqa: E402

URL = "s3://bkt/202401020900_example-experiment/region_R1/"


def _listing(**overrides):
    objs = {"cell_metadata.csv": (23, "INTELLIGENT_TIERING"),
            "cell_by_gene.csv": (157, "INTELLIGENT_TIERING"),
            "images/micron_to_mosaic_pixel_transform.csv": (1, "STANDARD"),
            "detected_transcripts.parquet": (2275, "INTELLIGENT_TIERING"),
            "detected_transcripts.csv": (11957, "DEEP_ARCHIVE"),
            "images/mosaic_DAPI_z3.tif": (11916, "DEEP_ARCHIVE")}
    objs.update(overrides)
    return {k: {"size": v[0], "storage": v[1], "etag": f"e-{k}"}
            for k, v in objs.items() if v is not None}


class _Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(s3, "CACHE", self.tmp.name)
        p.start(); self.addCleanup(p.stop)
        self.fetched = []

        def fake_fetch(url, rel, size, dest):
            self.fetched.append(rel)
            path = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(b"x" * size)
            return "fetched"
        p = mock.patch.object(s3, "_fetch", side_effect=fake_fetch)
        p.start(); self.addCleanup(p.stop)

    def listing(self, **kw):
        p = mock.patch.object(s3, "list_objects", return_value=_listing(**kw))
        p.start(); self.addCleanup(p.stop)


class ParseTests(unittest.TestCase):
    def test_urls(self):
        self.assertTrue(s3.is_s3(" S3://b/x "))
        self.assertFalse(s3.is_s3("/data/x"))
        self.assertEqual(s3.parse("s3://b/a/region_R1"), ("b", "a/region_R1/"))
        self.assertEqual(s3.parse("s3://b/a/region_R1///"), ("b", "a/region_R1/"))
        with self.assertRaises(ValueError):
            s3.parse("s3://bucket-only")

    def test_the_job_key_carries_the_experiment_timestamp(self):
        """Every region folder is `region_R1`; the key names the job's
        artifacts, so the experiment's timestamp has to be in it. The frontend
        computes the same key (tests/test_job_name_ui.mjs)."""
        self.assertEqual(s3.auto_key(URL), "s3_202401020900_R1")

    def test_the_cache_mirrors_the_bucket(self):
        with mock.patch.object(s3, "CACHE", "/c"):
            self.assertEqual(s3.local_dir(URL), "/c/bkt/" + s3.parse(URL)[1].rstrip("/"))


class PrepareTests(_Cache):
    def test_tables_now_transcripts_later_dapi_only_if_asked(self):
        self.listing()
        dest, rec = s3.prepare(URL)
        self.assertEqual(sorted(self.fetched), sorted(
            ["cell_metadata.csv", "cell_by_gene.csv",
             "images/micron_to_mosaic_pixel_transform.csv"]))
        f = rec["files"]
        self.assertEqual(f["detected_transcripts.parquet"]["status"], "pending")
        self.assertEqual(f["detected_transcripts.parquet"]["when"], "background")
        self.assertEqual(f["images/mosaic_DAPI_z3.tif"]["status"], "not requested")
        self.assertNotIn("detected_transcripts.csv", f, "the CSV is never the source")
        self.assertEqual(json.load(open(os.path.join(dest, s3.RECORD)))["url"], URL)

    def test_a_second_job_on_the_path_sees_what_is_already_cached(self):
        """Re-preparing must not report a cached file as pending again."""
        self.listing()
        dest, _ = s3.prepare(URL)
        s3._background(dest)
        self.fetched.clear()
        _, rec = s3.prepare(URL)
        self.assertEqual(rec["files"]["detected_transcripts.parquet"]["status"], "fetched")
        self.assertEqual(self.fetched, [], "nothing fetched twice")

    def test_an_archived_dapi_is_reported_not_requested(self):
        self.listing()
        _, rec = s3.prepare(URL, include_dapi=True)
        self.assertEqual(rec["files"]["images/mosaic_DAPI_z3.tif"]["status"], "archived")
        self.assertNotIn("images/mosaic_DAPI_z3.tif", self.fetched)

    def test_an_available_dapi_is_queued_when_asked(self):
        self.listing(**{"images/mosaic_DAPI_z3.tif": (5, "STANDARD")})
        _, rec = s3.prepare(URL, include_dapi=True)
        self.assertEqual(rec["files"]["images/mosaic_DAPI_z3.tif"]["status"], "pending")

    def test_an_archived_required_table_refuses_before_fetching_anything(self):
        self.listing(**{"cell_by_gene.csv": (157, "DEEP_ARCHIVE")})
        with self.assertRaisesRegex(ValueError, "cell_by_gene.csv is archived.*restore"):
            s3.prepare(URL)
        self.assertEqual(self.fetched, [])

    def test_a_missing_required_table_is_named(self):
        self.listing(**{"cell_metadata.csv": None})
        with self.assertRaisesRegex(ValueError, "cell_metadata.csv is absent"):
            s3.prepare(URL)

    def test_an_empty_or_inaccessible_path_is_named(self):
        with mock.patch.object(s3, "list_objects", return_value={}):
            with self.assertRaisesRegex(ValueError, "nothing found"):
                s3.prepare(URL)


class FetchTests(unittest.TestCase):
    def test_a_file_already_here_at_the_listed_size_is_not_fetched(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "cell_metadata.csv"), "wb").write(b"x" * 23)
            with mock.patch.object(s3, "_aws") as aws:
                self.assertEqual(s3._fetch(URL, "cell_metadata.csv", 23, d), "fetched")
                aws.assert_not_called()


class BackgroundTests(_Cache):
    def test_the_worker_fetches_what_is_pending_and_records_it(self):
        self.listing()
        dest, _ = s3.prepare(URL)
        self.fetched.clear()
        s3._background(dest)
        self.assertEqual(self.fetched, ["detected_transcripts.parquet"])
        rec = s3.read_record(dest)
        self.assertEqual(rec["files"]["detected_transcripts.parquet"]["status"], "fetched")
        self.assertFalse(os.path.exists(os.path.join(dest, ".s3_fetch.lock")))

    def test_a_second_worker_leaves_it_to_the_first(self):
        self.listing()
        dest, _ = s3.prepare(URL)
        open(os.path.join(dest, ".s3_fetch.lock"), "w").close()
        self.fetched.clear()
        s3._background(dest)
        self.assertEqual(self.fetched, [])


class NetworkTests(unittest.TestCase):
    """2026-10-20: the server's DNS dropped lookups for seconds at a time, and
    the UI showed AWS's raw "Could not connect to the endpoint URL", which read
    like a wrong path. A network failure is now retried once, then named."""

    NET = "aws: [ERROR]: Could not connect to the endpoint URL: \"https://b.s3.amazonaws.com/\""

    def run_with(self, *results):
        calls = iter(results)
        def fake(cmd, **kw):
            rc, err, out = next(calls)
            return mock.Mock(returncode=rc, stderr=err, stdout=out)
        p1 = mock.patch.object(s3.subprocess, "run", side_effect=fake)
        p2 = mock.patch.object(s3, "RETRY_DELAY_S", 0)
        self.run = p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_a_blip_is_retried_and_succeeds(self):
        self.run_with((255, self.NET, ""), (0, "", "ok"))
        self.assertEqual(s3._aws("s3", "ls"), "ok")
        self.assertEqual(self.run.call_count, 2)

    def test_a_lasting_outage_is_named_as_the_servers_network(self):
        self.run_with((255, self.NET, ""), (255, self.NET, ""))
        with self.assertRaises(s3.NetworkError) as cm:
            s3._aws("s3", "ls")
        self.assertIn("not your path", str(cm.exception))
        self.assertIn("Could not connect", str(cm.exception), "keeps AWS's own words")
        self.assertIsInstance(cm.exception, ValueError)

    def test_dns_failures_count_as_network(self):
        for msg in ("Temporary failure in name resolution", "Could not resolve host: x",
                    "Connect timeout on endpoint URL"):
            self.assertTrue(s3._is_network(msg), msg)

    def test_a_real_s3_error_is_not_retried_or_renamed(self):
        self.run_with((255, "An error occurred (AccessDenied) when calling ListObjectsV2", ""))
        with self.assertRaises(ValueError) as cm:
            s3._aws("s3", "ls")
        self.assertNotIsInstance(cm.exception, s3.NetworkError)
        self.assertIn("AccessDenied", str(cm.exception))
        self.assertEqual(self.run.call_count, 1)

    def test_the_endpoint_answers_503_not_400(self):
        from web import server
        from fastapi import HTTPException
        with mock.patch.object(s3, "prepare", side_effect=s3.NetworkError("could not reach S3")):
            with self.assertRaises(HTTPException) as cm:
                server.create_job(server.JobCreateReq(exp_path=URL))
        self.assertEqual(cm.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main(verbosity=2)
