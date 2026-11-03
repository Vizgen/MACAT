"""The MERSCOPE Vizualizer export (allen_alignment/vizualizer.py).

The format is not ours to invent: it is the one built and import-tested in a
notebook against the MERSCOPE Vizualizer on 2026-10-16. A whole metadata table
with labels appended had crashed the Vizualizer before that, so the port is
held to that notebook's rules on a synthetic table.
"""
import io
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

import pandas as pd

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

from allen_alignment import planes, vizualizer as viz      # noqa: E402

SHAPE, RES = (528, 320, 456), 25.0
FAR = 99999.0                                     # well past every face


def _rows():
    """(EntityID, id, acronym, name, parent, path, status, ap, si, lr)."""
    return [
        ("101", 788, "PAA", "Piriform-amygdalar area", "OLF",
         "root>grey>CH>CTX>CTXpl>OLF>PAA", "mapped", 0, 0, 0),
        ("102", 8, "grey", "Basic cell groups and regions", "root",
         "root>grey", "mapped", 0, 0, 0),
        ("103", 997, "root", "root", None, "root", "mapped", 0, 0, 0),
        ("104", 0, None, None, None, None, "unmapped", 0, 0, 0),
        ("105", 0, None, None, None, None, "unmapped", -FAR, 0, 0),
        ("106", 211, "RSPv2/3", "Retrosplenial area, ventral part, layer 2/3",
         "RSPv", "root>grey>CH>CTX>CTXpl>Isocortex>RSP>RSPv>RSPv2/3",
         "mapped", 0, 0, 0),
        # mapped yet "beyond" by coordinates: never relabelled -- only cells the
        # zero-fill actually left unlabelled can be beyond the atlas
        ("107", 788, "PAA", "Piriform-amygdalar area", "OLF",
         "root>grey>CH>CTX>CTXpl>OLF>PAA", "mapped", FAR, 0, 0),
    ]


def _write_src(path, rows=None):
    cols = ["EntityID", "fov", "allen_region_id", "allen_acronym", "allen_name",
            "allen_parent", "allen_ontology_path", "mapping_status",
            "atlas_ap_um", "atlas_si_um", "atlas_lr_um"]
    data = [(r[0], 1) + r[1:] for r in (rows or _rows())]
    pd.DataFrame(data, columns=cols).to_csv(path, index=False)
    return path


class LabelTests(unittest.TestCase):
    def test_the_notebooks_examples(self):
        self.assertEqual(viz.label("RSPv2/3"), "RSPv2-3")
        self.assertEqual(viz.label("Cortical amygdalar area, posterior part"),
                         "Cortical amygdalar area posterior part")
        self.assertEqual(viz.label("a_b  c"), "a-b c")

    def test_output_name_follows_the_source(self):
        self.assertEqual(
            viz.output_name("/x/cell_metadata_with_allen_regions_tr_1_curated.csv"),
            "TO_MERSCOPE_VIZUALIZER_allen_regions_tr_1_curated.csv")
        with self.assertRaises(ValueError):
            viz.output_name("/x/region_summary.csv")


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = _write_src(os.path.join(
            self.tmp.name, "cell_metadata_with_allen_regions_t.csv"))

    def test_columns_and_fills(self):
        t = viz.table(self.src)
        self.assertEqual(list(t.columns), viz.BASE_COLS + [
            f"ontology_level_{k}" for k in range(1, 9)])
        self.assertEqual(t.index.name, "EntityID")
        self.assertEqual(t.loc["101", "allen_region_id"], "id788")
        self.assertEqual(t.loc["104"].unique().tolist(), ["unmapped"])
        self.assertEqual(t.loc["103", "allen_parent"], "root")

    def test_levels_drop_root_and_carry_the_deepest_down(self):
        t = viz.table(self.src)
        lv = [f"ontology_level_{k}" for k in range(1, 9)]
        self.assertEqual(t.loc["101", lv].tolist(),
                         ["grey", "CH", "CTX", "CTXpl", "OLF", "PAA", "PAA", "PAA"])
        self.assertEqual(t.loc["102", lv].unique().tolist(), ["grey"])
        self.assertEqual(t.loc["103", lv].unique().tolist(), ["root"])
        self.assertEqual(t.loc["106", "ontology_level_8"], "RSPv2-3")

    def test_beyond_atlas_only_with_geometry_and_only_for_unmapped(self):
        plain = viz.table(self.src)
        self.assertEqual(plain.loc["105", "mapping_status"], "unmapped")
        t = viz.table(self.src, atlas_shape=SHAPE, res=RES)
        self.assertEqual(t.loc["105"].unique().tolist(), ["beyond atlas"])
        self.assertEqual(t.loc["104", "mapping_status"], "unmapped")
        self.assertEqual(t.loc["107", "mapping_status"], "mapped")

    def test_the_beyond_rule_is_the_overhang_rule(self):
        """The export and the Alignment record must agree on which cells are
        past the atlas; both go through planes.beyond_atlas."""
        pts = [[-FAR, 0, 0], [0, 0, 0]]
        self.assertEqual(planes.beyond_atlas(pts, SHAPE, RES)[0].tolist(),
                         [True, False])

    def test_every_value_is_safe_categorical_text(self):
        t = viz.table(self.src, atlas_shape=SHAPE, res=RES)
        self.assertFalse(t.apply(lambda s: s.str.contains(r"[^A-Za-z0-9\- ]"))
                         .any().any())
        self.assertFalse(t.isna().any().any())

    def test_cleaning_that_would_merge_two_regions_is_refused(self):
        rows = _rows()[:1] + [("201", 999, "PAA", "Piriform amygdalar area",
                               "OLF", "root>grey>CH>CTX>CTXpl>OLF>PAA",
                               "mapped", 0, 0, 0)]
        _write_src(self.src, rows)
        with self.assertRaisesRegex(ValueError, "more than one region id|merge"):
            viz.table(self.src)

    def test_a_path_that_disagrees_with_its_acronym_is_refused(self):
        rows = [("301", 788, "PAA", "x", "OLF", "root>grey>CH", "mapped", 0, 0, 0)]
        _write_src(self.src, rows)
        with self.assertRaisesRegex(ValueError, "ontology path"):
            viz.table(self.src)

    def test_duplicate_ids_are_refused(self):
        _write_src(self.src, _rows()[:1] * 2)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            viz.table(self.src)


class ServerTests(unittest.TestCase):
    """Built on request, cached on its source, rebuilt when the source changes,
    and shipped in report.zip under the view it describes."""

    def setUp(self):
        from web import jobs, server
        self.jobs, self.server = jobs, server
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(jobs, "JOBS_DIR", os.path.join(self.tmp.name, "jobs"))
        p.start(); self.addCleanup(p.stop)
        self.src = _write_src(os.path.join(
            self.tmp.name, "cell_metadata_with_allen_regions_t_curated.csv"))
        self.job = {"id": "vj", "name": "my section", "tag": "_t", "state": "COMPLETED",
                    "ap_idx": 300, "ap_res_um": 25.0,
                    "orientation": {"angle": 0, "flip": "none", "plane": "coronal"},
                    "register": {"status": "done", "result": {}},
                    "manual": {"status": "done", "result": {"csv_path": self.src}}}
        os.makedirs(jobs.job_dir("vj"))
        jobs.save(self.job)

    def test_built_next_to_its_source_and_reused(self):
        path = self.server._vizualizer_csv(self.job)
        self.assertEqual(path, os.path.join(
            self.tmp.name, "TO_MERSCOPE_VIZUALIZER_allen_regions_t_curated.csv"))
        m1 = os.stat(path).st_mtime_ns
        self.assertEqual(self.server._vizualizer_csv(self.job), path)
        self.assertEqual(os.stat(path).st_mtime_ns, m1, "rebuilt without cause")
        self.assertIn("beyond atlas", open(path).read())

    def test_rebuilt_when_the_source_is_newer(self):
        path = self.server._vizualizer_csv(self.job)
        time.sleep(0.01)
        _write_src(self.src, _rows()[:2])
        self.assertEqual(len(pd.read_csv(self.server._vizualizer_csv(self.job))), 2)

    def test_the_download_is_named_after_the_job(self):
        resp = self.server.job_vizualizer_csv("vj")
        self.assertIn("my_section", resp.headers["content-disposition"])

    def test_a_short_tag_is_renamed_as_a_token_not_a_substring(self):
        """Regression: _download_name used str.replace, so tag 't' was also
        replaced inside 'curated'. Found by this file's fixture."""
        self.assertEqual(
            self.server._download_name(self.job, "/x/region_summary_t_curated.csv"),
            "region_summary_my_section_curated.csv")

    def test_it_ships_in_report_zip(self):
        resp = self.server.job_report_zip("vj")
        z = zipfile.ZipFile(io.BytesIO(resp.body))
        names = [n for n in z.namelist() if n.startswith("merscope_vizualizer_")]
        self.assertEqual(names, [
            "merscope_vizualizer_curated/TO_MERSCOPE_VIZUALIZER_allen_regions_my_section_curated.csv"])
        import json
        m = json.loads(z.read("manifest.json"))
        self.assertEqual(m["merscope_vizualizer"]["derived_from"], "curated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
