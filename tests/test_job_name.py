"""A job's NAME is a label; its TAG keys the artifacts.

Keeping those separate is the whole design. Jobs on one section deliberately
share artifacts, so a label must never reach a path on
disk -- otherwise renaming a job would orphan its outputs or split one section's
artifacts into two sets. The rename happens on the way OUT instead: the tag is
swapped for the label in the download's filename.

The label reaches Content-Disposition and zip entry names, so it also has to be
sanitised. A name like "../../etc/passwd" must not be able to steer either.
"""
import os
import sys
import unittest

_pre = os.path.join(sys.prefix, "lib", "libstdc++.so.6")
if os.path.exists(_pre) and _pre not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = _pre + (
        ":" + os.environ["LD_PRELOAD"] if os.environ.get("LD_PRELOAD") else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from web.jobs import safe_name  # noqa: E402
from web.server import _download_name  # noqa: E402

TAG = "_tr_202401020900_R1"
CORE = "tr_202401020900_R1"


class SafeNameTests(unittest.TestCase):

    def test_a_plain_label_survives(self):
        self.assertEqual(safe_name("region_R1"), "region_R1")
        self.assertEqual(safe_name("Sample A11 rep2"), "Sample_A11_rep2")

    def test_path_separators_and_traversal_cannot_survive(self):
        """This string ends up in Content-Disposition and in zip entry names."""
        for hostile in ("../../etc/passwd", "a/b", "a\\b", "..", "./x"):
            out = safe_name(hostile, "fallback")
            self.assertNotIn("/", out)
            self.assertNotIn("\\", out)
            self.assertNotIn("..", out)
            self.assertFalse(out.startswith("."), f"{out!r} starts with a dot")

    def test_quotes_and_control_characters_cannot_survive(self):
        """A quote would terminate the filename in the header."""
        for hostile in ('a"b', "a';b", "a\nb", "a\rb", "a\tb", 'x";y="z'):
            out = safe_name(hostile, "fallback")
            for bad in ('"', "'", "\n", "\r", "\t", ";"):
                self.assertNotIn(bad, out, f"{bad!r} survived in {out!r}")

    def test_an_empty_or_useless_label_falls_back(self):
        for empty in ("", "   ", "___", "///", None):
            self.assertEqual(safe_name(empty, "fallback"), "fallback")

    def test_the_label_is_capped(self):
        self.assertLessEqual(len(safe_name("x" * 500, "f")), 120)


class DownloadNameTests(unittest.TestCase):

    def job(self, name):
        return {"tag": TAG, "name": name}

    def test_the_default_label_leaves_the_filename_byte_identical(self):
        """The overwhelming majority of jobs are never renamed, and their
        downloads must not change at all."""
        f = f"curated_result_{CORE}_curated.png"
        self.assertEqual(_download_name(self.job(CORE), f), f)

    def test_a_custom_label_replaces_the_tag(self):
        self.assertEqual(
            _download_name(self.job("Sample A11 rep2"),
                           f"curated_result_{CORE}_curated.png"),
            "curated_result_Sample_A11_rep2_curated.png")
        self.assertEqual(
            _download_name(self.job("Sample A11 rep2"),
                           f"cell_metadata_with_allen_regions_{CORE}.csv"),
            "cell_metadata_with_allen_regions_Sample_A11_rep2.csv")

    def test_a_file_that_does_not_carry_the_tag_is_prefixed_not_mangled(self):
        """Some artifacts are not tagged. Dropping the label entirely would make
        them anonymous in a folder of extracted reports."""
        self.assertEqual(_download_name(self.job("Sample A11"), "manifest.json"),
                         "Sample_A11_manifest.json")

    def test_a_hostile_label_cannot_escape_the_filename(self):
        out = _download_name(self.job("../../etc/passwd"),
                             f"curated_result_{CORE}.png")
        self.assertNotIn("/", out)
        self.assertNotIn("..", out)

    def test_no_job_means_the_on_disk_name(self):
        f = f"curated_result_{CORE}.png"
        self.assertEqual(_download_name(None, f), f)
        self.assertEqual(_download_name({}, f), f)

    def test_a_job_without_a_tag_is_left_alone(self):
        self.assertEqual(_download_name({"tag": "", "name": "x"}, "a_b.png"),
                         "a_b.png")

    def test_only_the_basename_is_returned(self):
        """It is a filename, not a path; returning a directory component would
        put a separator into the header."""
        out = _download_name(self.job("Sample"), f"/abs/dir/curated_{CORE}.png")
        self.assertNotIn("/", out)


class RouteRegistrationTests(unittest.TestCase):
    """/api/download must actually be routed.

    Not hypothetical: editing this handler dropped its decorator, and every
    download in the app 404'd while the module still imported cleanly and every
    other test passed.
    """

    def test_download_is_registered_and_takes_a_job_parameter(self):
        from web import server
        routes = {getattr(r, "path", None) for r in server.app.routes}
        self.assertIn("/api/download", routes)
        import inspect
        self.assertIn("job", inspect.signature(server.download).parameters)

    def test_the_endpoints_the_results_step_downloads_from_are_registered(self):
        from web import server
        routes = {getattr(r, "path", None) for r in server.app.routes}
        for path in ("/api/download", "/api/jobs/{jid}/report.zip"):
            self.assertIn(path, routes, f"{path} is not routed")


class TagIndependenceTests(unittest.TestCase):

    def test_the_tag_is_derived_from_the_section_not_the_label(self):
        """Read off the source: the tag must be built from `auto`, never from a
        user-supplied name, or renaming would move artifacts on disk."""
        import re
        with open(os.path.join(_STUDY, "web", "jobs.py")) as fh:
            src = fh.read()
        body = src[src.index("def create("):src.index("def create(") + 1800]
        m = re.search(r'^\s*tag = .*$', body, re.M)
        self.assertIsNotNone(m, "the tag assignment moved")
        self.assertIn("auto", m.group(0),
                      "the tag is no longer derived from the section; a custom "
                      "job name would now change artifact paths on disk")


if __name__ == "__main__":
    unittest.main()
