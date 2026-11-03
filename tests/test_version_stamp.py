"""The report manifest must say which code produced it.

A report .zip is a deliverable -- it goes into a paper, a slide, a handoff -- and
its manifest recorded which experiment, which atlas and which AP slice but
nothing about the software. The labelling logic moves, so two archives with
identical inputs could differ because the code between them changed, with
nothing in either one saying so.

What these pin is mostly HONESTY rather than presence: a version that is really
a fallback must say so, and a tree with uncommitted changes must admit the run
is not reproducible. A stamp that quietly guesses is worse than no stamp, because
it reads as authority.
"""
import os
import sys
import unittest

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import version  # noqa: E402

FIELDS = {"version", "version_source", "git_sha", "git_describe", "dirty"}


class BuildInfoTests(unittest.TestCase):

    def test_every_field_is_present(self):
        info = version.build_info()
        self.assertTrue(FIELDS <= set(info),
                        f"missing {FIELDS - set(info)}")

    def test_build_info_hands_back_a_copy(self):
        """A caller that mutated the shared dict would change what every LATER
        report claims about itself."""
        a = version.build_info()
        a["version"] = "tampered"
        self.assertNotEqual(version.build_info()["version"], "tampered")

    def test_dirty_is_a_bool_or_an_honest_none(self):
        """Never a string. `"false"` is truthy, and this is the field a reader
        checks to decide whether to trust the SHA."""
        self.assertIn(type(version.build_info()["dirty"]), (bool, type(None)))

    def test_a_dirty_tree_says_the_run_is_not_reproducible(self):
        info = version.build_info()
        if info["dirty"]:
            self.assertIn("not reproducible", info.get("note", "").lower(),
                          "dirty is set but nothing warns the reader")

    def test_the_version_source_is_named(self):
        info = version.build_info()
        self.assertIn("git tag" if info["version_source"] == "git tag"
                      else "fallback", info["version_source"])

    def test_a_fallback_version_is_never_passed_off_as_a_tag(self):
        """The failure that would matter: reporting 0.0.0-unknown, or a stale
        compiled-in constant, as though it had come from a tag."""
        info = version.build_info()
        if info["version"] == version._FALLBACK_VERSION:
            self.assertIn("fallback", info["version_source"])

    def test_without_a_git_checkout_it_reports_that_rather_than_guessing(self):
        real = version._git
        try:
            version._git = lambda *a: None          # no git, no .git, no answer
            info = version._compute()
        finally:
            version._git = real
        self.assertIsNone(info["git_sha"])
        self.assertIsNone(info["dirty"], "unknown must not be reported as clean")
        self.assertIn("fallback", info["version_source"])
        self.assertIn("cannot be traced", info["note"])

    def test_a_tag_becomes_the_version(self):
        """Tags are the source of truth, not a constant somebody has to
        remember to bump."""
        real = version._git
        try:
            version._git = lambda *a: {
                ("rev-parse", "HEAD"): "a" * 40,
                ("describe", "--tags", "--abbrev=0"): "v0.2.0",
                ("describe", "--tags", "--always", "--dirty"): "v0.2.0-3-gabc",
                ("status", "--porcelain"): None,
            }.get(a, None)
            info = version._compute()
        finally:
            version._git = real
        self.assertEqual(info["version"], "v0.2.0")
        self.assertEqual(info["version_source"], "git tag")
        self.assertEqual(info["git_describe"], "v0.2.0-3-gabc")


class ManifestStampTests(unittest.TestCase):

    def test_the_report_manifest_carries_the_software_block(self):
        src = os.path.join(_STUDY, "web", "server.py")
        with open(src) as fh:
            s = fh.read()
        self.assertIn('"software": version.build_info()', s,
                      "report.zip's manifest no longer records the software")
        self.assertIn("import version", s)


if __name__ == "__main__":
    unittest.main()
