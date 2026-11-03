"""Deleting jobs from the Resume list, and downloading several reports at once.

Deleting is the one irreversible thing the app does, and jobs on one section
SHARE their bundle, fit and per-cell CSVs. So the rules pinned here are about
what must SURVIVE: files a remaining job still points to, another section's
files that merely contain a short tag, anything outside the store, and every
job at all when the request names a running or unknown job.
"""
import io
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from fastapi import HTTPException                     # noqa: E402

from web import jobs, server                           # noqa: E402


class _Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "store")
        os.makedirs(os.path.join(self.root, "jobs"))
        for name, val in (("OUT_ROOT", self.root),
                          ("JOBS_DIR", os.path.join(self.root, "jobs"))):
            p = mock.patch.object(jobs, name, val)
            p.start(); self.addCleanup(p.stop)

    def f(self, name, where=None):
        path = os.path.join(where or self.root, name)
        with open(path, "w") as fh:
            fh.write(name)
        return path

    def job(self, jid, tag, state="COMPLETED", reg=None, man=None, **extra):
        j = {"id": jid, "name": jid, "tag": tag, "state": state,
             "created": time.time(), "updated": time.time(),
             "register": {"status": "done", "result": reg or {}},
             "manual": {"status": "done", "result": man} if man else None}
        j.update(extra)
        os.makedirs(jobs.job_dir(jid), exist_ok=True)
        jobs.save(j)
        return j

    def exists(self, *names):
        return [os.path.exists(os.path.join(self.root, n)) for n in names]


class DeleteTests(_Store):
    def setUp(self):
        super().setUp()
        # two jobs on section `secX` share the registration outputs
        shared = {"bundle_path": self.f("manual_bundle_secX.npz"),
                  "csv_path": self.f("cell_metadata_with_allen_regions_secX.csv")}
        self.job("A", "_secX", reg=shared,
                 man={"csv_path": self.f("cell_metadata_with_allen_regions_secX_curated.csv")})
        self.job("B", "_secX", reg=shared)
        self.f("region_summary_secX_curated.csv")            # derived, unreferenced
        self.f("hex_expression_qc_secX_curated_100um.png")

    def test_a_shared_file_stays_while_another_job_uses_it(self):
        out = jobs.delete_jobs(["A"])
        self.assertEqual(out["deleted"], ["A"])
        self.assertFalse(os.path.exists(jobs.job_dir("A")))
        self.assertEqual(self.exists("manual_bundle_secX.npz",
                                     "cell_metadata_with_allen_regions_secX.csv"),
                         [True, True], "B still needs these")
        self.assertEqual(self.exists("cell_metadata_with_allen_regions_secX_curated.csv"),
                         [False])
        # B still has the section's tag, so its derived files stay too
        self.assertEqual(self.exists("region_summary_secX_curated.csv"), [True])
        self.assertEqual(out["files_kept_shared"], 2)

    def test_the_last_job_on_a_section_takes_the_section_with_it(self):
        jobs.delete_jobs(["A", "B"])
        left = sorted(os.listdir(self.root))
        self.assertEqual(left, ["jobs"])

    def test_a_short_tag_never_matches_another_sections_files(self):
        """`R1` as a substring is in `tr_x_R1`. The exact-pattern match must
        not take that other section's files."""
        self.job("short", "_R1", reg={"csv_path": self.f(
            "cell_metadata_with_allen_regions_R1.csv")})
        other = self.f("region_summary_tr_x_R1_curated.csv")
        self.f("region_summary_R1.csv")
        jobs.delete_jobs(["short"])
        self.assertTrue(os.path.exists(other))
        self.assertEqual(self.exists("region_summary_R1.csv",
                                     "cell_metadata_with_allen_regions_R1.csv"),
                         [False, False])

    def test_nothing_outside_the_store_is_touched(self):
        outside = self.f("cell_metadata_with_allen_regions_far.csv", self.tmp.name)
        self.job("far", "_far", reg={"csv_path": outside})
        jobs.delete_jobs(["far"])
        self.assertTrue(os.path.exists(outside))

    def test_a_running_job_stops_the_whole_request(self):
        self.job("run", "_secY", state="AP_CONFIRMED")
        j = jobs.load("run"); j["register"] = {"status": "running"}; jobs.save(j)
        with self.assertRaisesRegex(ValueError, "still running"):
            jobs.delete_jobs(["A", "run"])
        self.assertTrue(os.path.exists(jobs.job_dir("A")), "A was deleted anyway")

    def test_an_unknown_id_stops_the_whole_request(self):
        with self.assertRaises(KeyError):
            jobs.delete_jobs(["A", "nope"])
        self.assertTrue(os.path.exists(jobs.job_dir("A")))

    def test_the_endpoint_maps_refusals(self):
        with self.assertRaises(HTTPException) as cm:
            server.delete_jobs(server.DeleteJobsReq(ids=["nope"]))
        self.assertEqual(cm.exception.status_code, 404)
        self.job("fin", "_secZ", state="FINALIZING")
        with self.assertRaises(HTTPException) as cm:
            server.delete_jobs(server.DeleteJobsReq(ids=["fin"]))
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(server.delete_jobs(server.DeleteJobsReq(ids=["B"]))["deleted"], ["B"])


class JobsDirTests(_Store):
    def test_the_note_gets_the_real_jobs_folder(self):
        """The note under the Resume list must name where jobs ARE, which
        ALLEN_OUT_ROOT can move -- not a hardcoded default."""
        got = server.jobs_dir()
        self.assertEqual(got["jobs_dir"], os.path.join(self.root, "jobs"))
        self.assertTrue(got["server_tz"])


class CreatedTimeTests(_Store):
    """Created time follows the SERVER's time zone: the clock jobs.create stamps
    the job ID with. Checked under a zone that is not UTC, so a formatter that
    quietly assumed UTC would fail here."""

    def _in_zone(self, tz):
        old = os.environ.get("TZ")
        os.environ["TZ"] = tz
        time.tzset()

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()
        self.addCleanup(restore)

    def test_created_time_agrees_with_the_job_id_in_any_server_zone(self):
        for tz in ("UTC", "America/New_York", "Asia/Taipei"):
            self._in_zone(tz)
            ts = 1791390224.81266
            jid = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts)) + "_abc123"
            shown = server._server_time(ts)                     # "YYYY-MM-DD HH:MM"
            self.assertEqual(shown.replace("-", "").replace(" ", "_").replace(":", ""),
                             jid[:13], tz)

    def test_every_job_in_the_list_carries_it(self):
        self.job("t1", "_s")
        row = [j for j in server.list_all_jobs() if j["id"] == "t1"][0]
        self.assertEqual(row["created_text"], server._server_time(row["created"]))


class BulkDownloadTests(_Store):
    def test_only_completed_jobs_are_bundled(self):
        self.job("done1", "_s1", reg={"csv_path": self.f("cell_metadata_with_allen_regions_s1.csv")})
        self.job("done2", "_s2", reg={"csv_path": self.f("cell_metadata_with_allen_regions_s2.csv")})
        self.job("failed", "_s3", state="FAILED")
        resp = server.reports_zip("done1,failed,done2")
        with zipfile.ZipFile(resp.path) as z:
            names = sorted(z.namelist())
            self.assertEqual(names, ["done1_report.zip", "done2_report.zip"])
            inner = zipfile.ZipFile(io.BytesIO(z.read("done1_report.zip")))
            self.assertIn("manifest.json", inner.namelist())
        os.remove(resp.path)                 # the BackgroundTask's job, in a test

    def test_nothing_completed_is_a_400(self):
        self.job("ready", "_s4", state="INPUT_READY")
        with self.assertRaises(HTTPException) as cm:
            server.reports_zip("ready")
        self.assertEqual(cm.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
