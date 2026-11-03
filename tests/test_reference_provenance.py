"""The committed Allen reference must stay traceable, and raw.

`data/reference/allen_abc_region_by_gene.csv` is the only tracked file in this
repo derived from an external dataset, and the one thing the expression QC scores
everything against. Two ways it can quietly go wrong:

  1. the record of WHERE it came from drifts away from the builder, so a re-fetch
     pulls a different release and nobody notices;
  2. it gets rebuilt from the `-log2` release instead of `-raw`. The Allen
     release ships both with identical shape, identical non-zero count and
     identical dtype, so they are byte-for-byte the SAME SIZE and `ls` cannot
     tell them apart. Only the values can. The QC then applies its own log1p, so
     the mistake would log already-logged data and shift every correlation in
     step 6 with nothing in the output to show it.
"""
import os
import re
import unittest

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOC = os.path.join(_STUDY, "docs", "reference_provenance.md")
_BUILDER = os.path.join(_STUDY, "scripts", "data", "fetch_abc_reference.py")
_CSV = os.path.join(_STUDY, "data", "reference", "allen_abc_region_by_gene.csv")


def _read(p):
    with open(p) as fh:
        return fh.read()


class ProvenanceRecordTests(unittest.TestCase):

    def test_the_record_exists(self):
        self.assertTrue(os.path.exists(_DOC),
                        "docs/reference_provenance.md is gone; the committed "
                        "reference is no longer traceable to a release")

    def test_it_names_the_bucket_dataset_and_both_release_versions(self):
        doc = _read(_DOC)
        for needed in ("s3://allen-brain-cell-atlas/",
                       "MERFISH-C57BL6J-638850",
                       "20230830",      # expression release
                       "20231215"):     # CCF parcellation release
            self.assertIn(needed, doc, f"the record no longer names {needed!r}")

    def test_the_builder_points_at_the_record(self):
        """A provenance doc nobody is sent to is a doc nobody reads."""
        self.assertIn("docs/reference_provenance.md", _read(_BUILDER))

    def test_the_builder_and_the_record_agree_on_the_ccf_release(self):
        """The builder hard-codes a release directory in the VIEW path. If
        someone bumps it there and not here, the record silently describes a
        file that is no longer the one being built.

        The release is read from the VIEW ASSIGNMENT, not from anywhere in the
        file. A first version searched the whole source and matched the release
        quoted in this script's own docstring, so editing the real path left the
        test passing -- it was comparing the doc against a comment."""
        builder = _read(_BUILDER)
        m = re.search(r"^VIEW\s*=\s*(.+?)^\w", builder, re.M | re.S)
        self.assertIsNotNone(m, "the VIEW path assignment is gone")
        # the path is written as adjacent string literals across two lines
        view = "".join(re.findall(r'"([^"]*)"', m.group(1)))
        rel = re.search(r"MERFISH-C57BL6J-638850-CCF/(\d{8})", view)
        self.assertIsNotNone(
            rel, f"the VIEW path names no CCF release: {view!r}")
        self.assertIn(rel.group(1), _read(_DOC),
                      f"the builder reads CCF release {rel.group(1)}, which the "
                      f"provenance record does not mention")

    def test_the_licence_is_recorded_as_what_it_was_cleared_as(self):
        """Reviewed 2026-10-19: CC BY-NC 4.0, from both ABC dataset pages. This
        replaces the test that held the question open, as that test asked to be
        replaced once the licence was genuinely established.

        It pins the CONSEQUENCES, not just the label: noncommercial, attribution
        and the citation. Those are what someone redistributing the tables
        has to act on, and the label alone would let them be edited away."""
        doc = _read(_DOC).lower()
        self.assertIn("cc by-nc 4.0", doc)
        self.assertNotIn("not yet recorded", doc)
        for must in ("noncommercial", "attribution", "10.1038/s41586-023-06812-z",
                     "written permission"):
            self.assertIn(must, doc, f"the licence record lost {must!r}")

    def test_the_allen_files_are_excluded_from_the_code_license(self):
        """The repository is Apache-2.0; the Allen-derived files are not, and are
        not shipped: each user rebuilds them. NOTICE travels with redistributions
        (Apache-2.0, section 4d), so it is where the exclusion, the attribution
        and the pointer to the rebuild must stay."""
        notice = _read(os.path.join(_STUDY, "NOTICE"))
        self.assertIn("NOT covered by the Apache License", notice)
        self.assertIn("does NOT include", notice)
        self.assertIn("Rebuilding them", notice)
        for f in ("allen_abc_region_by_gene.csv", "allen_abc_section_by_gene.csv",
                  "allen_abc_sagittal_by_gene.csv", "structure_tree_safe_2017.csv"):
            self.assertIn(f, notice, f"{f} is not attributed in NOTICE")
        self.assertIn("CC BY-NC 4.0", notice)
        self.assertIn("NONCOMMERCIAL", notice)


class RawNotLog2Tests(unittest.TestCase):

    def test_the_builder_refuses_log_transformed_input(self):
        """The guard must exist and be CALLED, not merely defined."""
        b = _read(_BUILDER)
        self.assertIn("def assert_raw_counts", b)
        # re.M matters: without it `^` anchors to the start of the FILE, and the
        # check fails against a script that does call the guard.
        self.assertIsNotNone(
            re.search(r"^\s+assert_raw_counts\(", b, re.M),
            "assert_raw_counts is defined but never called")

    def test_the_record_explains_the_trap(self):
        doc = _read(_DOC)
        self.assertIn("log2", doc)
        self.assertIn("same size", doc.lower(),
                      "the record no longer says the two releases are "
                      "indistinguishable by size, which is the whole trap")


@unittest.skipUnless(os.path.exists(_CSV), "reference table not present")
class ShippedTableTests(unittest.TestCase):

    def test_the_table_has_no_blank_probes(self):
        """Blanks are control probes, not genes. Leaving them in would put
        noise into every correlation."""
        with open(_CSV) as fh:
            header = fh.readline().strip().split(",")
        blanks = [c for c in header if c.lower().startswith("blank")]
        self.assertEqual(blanks, [], f"blank probes in the reference: {blanks}")

    def test_the_table_matches_the_shape_the_record_claims(self):
        with open(_CSV) as fh:
            header = fh.readline().strip().split(",")
            rows = sum(1 for _ in fh)
        genes = len(header) - 1                      # first column is `acronym`
        doc = _read(_DOC)
        self.assertIn(str(genes), doc,
                      f"the table has {genes} genes; the record does not say so")
        self.assertIn(str(rows), doc,
                      f"the table has {rows} regions; the record does not say so")


if __name__ == "__main__":
    unittest.main()


class RebuildRouteTests(unittest.TestCase):
    """A public release ships WITHOUT the Allen files (CC BY-NC 4.0), so the
    documented rebuild route is the only way a user gets them. It lives in
    data/reference/README.md, linked from the root README. These keep that route
    documented, findable, and its published checksums true."""

    _FILES = ("structure_tree_safe_2017.csv", "allen_abc_region_by_gene.csv",
              "allen_abc_section_by_gene.csv", "allen_abc_sagittal_by_gene.csv")

    def test_the_rebuild_route_is_documented_and_linked(self):
        doc = _read(os.path.join(_STUDY, "data", "reference", "README.md"))
        for must in ("## Rebuilding them", "environment_allen_reference.yml",
                     "scripts/data/fetch_allen_sources.py",
                     "scripts/data/fetch_abc_reference.py",
                     "scripts/data/fetch_abc_section_reference.py"):
            self.assertIn(must, doc, f"data/reference/README.md no longer documents {must!r}")
        readme = _read(os.path.join(_STUDY, "README.md"))
        self.assertIn("data/reference/README.md", readme,
                      "the root README no longer links to the rebuild route")
        for script in ("fetch_allen_sources.py", "fetch_abc_reference.py",
                       "fetch_abc_section_reference.py"):
            self.assertTrue(os.path.exists(os.path.join(_STUDY, "scripts", "data", script)))
        self.assertTrue(os.path.exists(os.path.join(_STUDY, "environment_allen_reference.yml")))

    def test_published_checksums_match_the_committed_files(self):
        """Verified 2026-10-19: rebuilt from the public bucket, all four came
        out byte-identical. If a table is regenerated on purpose, its checksum
        in data/reference/README.md has to move with it."""
        import hashlib
        doc = _read(os.path.join(_STUDY, "data", "reference", "README.md"))
        for f in self._FILES:
            path = os.path.join(_STUDY, "data", "reference", f)
            if not os.path.exists(path):
                continue                      # a public checkout before the rebuild
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            self.assertIn(f"`{f}` | `{digest}`", doc,
                          f"{f} no longer matches its published checksum")
