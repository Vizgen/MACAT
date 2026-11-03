"""A missing Allen reference file is reported by name, with how to build it.

A public copy of MACAT does not ship these files (CC BY-NC 4.0), so every new
user meets this until they build them. Before, registration failed with a bare
FileNotFoundError that said nothing about what the file is or where it comes
from.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from allen_alignment import reference_files as rf      # noqa: E402


class MessageTests(unittest.TestCase):
    def test_names_the_file_what_needs_it_and_the_build_step(self):
        e = rf.MissingReferenceFile("/x/data/reference/structure_tree_safe_2017.csv")
        msg = str(e)
        self.assertIn("/x/data/reference/structure_tree_safe_2017.csv", msg)
        self.assertIn("region hierarchy", msg)
        self.assertIn("Build the Allen reference files", msg)
        self.assertIn("Rebuilding them", msg)
        self.assertIsInstance(e, FileNotFoundError)   # existing handlers still catch it

    def test_require_passes_a_file_through(self):
        with tempfile.NamedTemporaryFile() as fh:
            self.assertEqual(rf.require(fh.name), fh.name)

    def test_missing_lists_only_what_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "allen_abc_region_by_gene.csv"), "w").close()
            gone = {os.path.basename(p) for p in rf.missing(d)}
        self.assertEqual(gone, set(rf.FILES) - {"allen_abc_region_by_gene.csv"})


class LoaderTests(unittest.TestCase):
    """The two loaders every Allen file goes through raise the clear error."""

    def test_structure_tree(self):
        from allen_alignment import atlas
        with mock.patch.object(atlas, "_stree_cache", None):
            with self.assertRaises(rf.MissingReferenceFile) as cm:
                atlas._load_structure_tree("/nonexistent/structure_tree_safe_2017.csv")
        self.assertIn("Build the Allen reference files", str(cm.exception))

    def test_expression_tables(self):
        from allen_alignment.qc.m4_expr_corr import load_allen_reference
        with self.assertRaises(rf.MissingReferenceFile) as cm:
            load_allen_reference("/nonexistent/allen_abc_region_by_gene.csv")
        self.assertIn("expression QC per region", str(cm.exception))


class BuildEnvironmentTests(unittest.TestCase):
    """uv and pip users build with requirements-allen-reference.txt, conda users
    with environment_allen_reference.yml. Both must pin the same versions, the
    ones the published checksums were reproduced with."""

    def test_the_two_build_files_pin_the_same_versions(self):
        import re
        with open(os.path.join(_STUDY, "environment_allen_reference.yml")) as fh:
            conda = dict(re.findall(r"^\s*-\s*([A-Za-z0-9_.-]+)=([0-9][^\s#]*)", fh.read(), re.M))
        conda.pop("python", None)
        with open(os.path.join(_STUDY, "requirements-allen-reference.txt")) as fh:
            pip = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s#]+)", fh.read(), re.M))
        self.assertTrue(pip)
        self.assertEqual(pip, conda)


if __name__ == "__main__":
    unittest.main()
