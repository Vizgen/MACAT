"""Resume-list notes (2026-10-23) and the known level on every job view.

Notes are kept in notes.json beside job.json, because registration and finalize
workers rewrite job.json from their own copy and would drop a note saved while
they run. Since v0.5.0 the known level comes from the job's own folder (its
expected.json answer key, web/answer_key.py; tests/test_answer_key.py), not from
a list of named sections.
"""
import os
import sys
import tempfile
import unittest

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import config                      # noqa: E402
from web import jobs               # noqa: E402


class NotesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = jobs.JOBS_DIR
        jobs.JOBS_DIR = self._tmp.name
        jobs._atomic_write(jobs._job_file("j1"), {"id": "j1", "state": "INPUT_READY"})

    def tearDown(self):
        jobs.JOBS_DIR = self._old
        self._tmp.cleanup()

    def test_a_note_round_trips_trimmed_and_capped(self):
        self.assertEqual(jobs.read_notes("j1"), "")
        self.assertEqual(jobs.set_notes("j1", "  good fit  "), "good fit")
        self.assertEqual(jobs.read_notes("j1"), "good fit")
        self.assertEqual(len(jobs.set_notes("j1", "x" * 2000)), jobs.MAX_NOTES)

    def test_a_worker_rewriting_job_json_keeps_the_note(self):
        stale = jobs.load("j1")
        jobs.set_notes("j1", "check CA1")
        jobs.save({**stale, "state": "REGISTERED"})
        self.assertEqual(jobs.read_notes("j1"), "check CA1")

    def test_unknown_job_is_refused(self):
        with self.assertRaises(KeyError):
            jobs.set_notes("nope", "x")


class KnownLevelTests(unittest.TestCase):
    """Every built Allen annotated section carries its answer in its folder."""
    def setUp(self):
        from web import server
        self.server = server

    def test_built_sections_show_their_level_from_their_own_folder(self):
        if not config.POSITIVE_CONTROL_ANSWERS:
            self.skipTest("no Allen annotated data built on this host")
        res = int(round(jobs.current_res_um()))
        for name, row in config.POSITIVE_CONTROL_ANSWERS.items():
            k = self.server._known_level({"exp_path": config.POSITIVE_CONTROLS[name]})
            self.assertEqual(k["idx"], int(row[f"idx_{res}um"]), name)
            self.assertEqual(k["res_um"], res)
            self.assertEqual(k["mirror"] is not None, row["plane"] == "sagittal", name)

    def test_a_folder_without_an_answer_key_has_none(self):
        self.assertIsNone(self.server._known_level({"exp_path": tempfile.mkdtemp()}))
        self.assertIsNone(self.server._known_level({}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
