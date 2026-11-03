"""Smoke tests for the read-only bundle endpoints in web.server.

Why this file exists: `/api/bundle` shipped with `NameError: name 'bundle' is
not defined`. Every job hits that endpoint on entering curation, so the app was
broken for every user, and nothing caught it -- no test had ever imported
web.server's route handlers. A unit test of the underlying module would not have
helped either, because the bug was in the endpoint's own local wiring.

These call the handler functions directly, which exercises the real body without
needing an HTTP client or a running server. They are skipped when no registration
bundle is on disk, in the same way the composite tests are.
"""
import glob
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


def _find_bundle():
    # only a bundle at the configured resolution; see tests/_bundles.py
    from _bundles import first_bundle
    return first_bundle()


_BUNDLE = _find_bundle()


def _find_report_job():
    """A completed job whose combined figure ALSO sits inside its panel zip.

    That overlap is the condition the dedup guards, so a job without it would
    make the test pass vacuously.
    """
    import json
    import zipfile
    # the same store the server reads (ALLEN_OUT_ROOT, else pipeline_output)
    from _bundles import ROOT
    for f in sorted(glob.glob(os.path.join(ROOT, "jobs", "*", "job.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        res = (d.get("manual") or {}).get("result") or {}
        fig, zp = res.get("fig_path"), res.get("figure_zip_path")
        if not (fig and zp and os.path.exists(fig) and os.path.exists(zp)):
            continue
        try:
            with zipfile.ZipFile(zp) as z:
                inner = set(z.namelist())
        except Exception:
            continue
        if os.path.basename(fig) in inner:
            return d.get("id") or os.path.basename(os.path.dirname(f))
    return None


_REPORT_JOB = _find_report_job()


@unittest.skipUnless(_BUNDLE, "no registration bundle at the configured resolution; see tests/_bundles.py")
class TestBundleEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server
        cls.path = _BUNDLE

    def test_bundle_meta_returns_every_key_the_frontend_reads(self):
        """The regression that motivated this file.

        CurationStep destructures all of these; a missing or exploding key takes
        the whole step down rather than degrading.
        """
        m = self.server.bundle_meta(self.path)
        for key in ("path", "tag", "ap", "exp_path", "lr0", "lr1", "si0", "si1",
                    "width", "height", "depths", "depth_default",
                    "has_dapi", "has_markers", "has_transcripts",
                    "atlas_provenance", "sampled_ap_idx", "sampled_ap_um"):
            self.assertIn(key, m, f"/api/bundle no longer returns {key!r}")
        self.assertIsInstance(m["has_markers"], bool)
        self.assertIsInstance(m["has_dapi"], bool)
        self.assertIsInstance(m["has_transcripts"], bool)
        self.assertGreater(m["width"], 0)
        self.assertGreater(m["height"], 0)
        self.assertLess(m["lr0"], m["lr1"])
        self.assertLess(m["si0"], m["si1"])

    def test_every_advertised_boundary_mode_actually_renders(self):
        """`depths` drives buttons in the UI, so an unrenderable mode is a dead
        button rather than a caught error."""
        m = self.server.bundle_meta(self.path)
        self.assertIn(m["depth_default"], m["depths"])
        for mode in m["depths"]:
            out = self.server.boundaries(self.path, mode)
            self.assertEqual(out["depth"], mode)
            self.assertIsInstance(out["features"], list)

    def test_markers_report_matches_what_bundle_meta_advertises(self):
        """has_markers must not disagree with /api/markers, or the button either
        appears with nothing behind it or hides a layer that works."""
        m = self.server.bundle_meta(self.path)
        if not m["has_markers"]:
            self.skipTest("panel does not support the marker background")
        rep = self.server.markers_report(self.path)
        self.assertGreaterEqual(sum(r["used"] for r in rep["regions"]), 1)
        for r in rep["regions"]:
            self.assertIn("color", r)
            self.assertEqual(r["used"], r["n_present"] >= rep["min_genes"])

    def test_cell_density_is_grayscale_with_dense_white(self):
        """No hue to compete with the coloured region overlay in step 5."""
        import io
        import numpy as np
        from PIL import Image
        px = np.asarray(Image.open(io.BytesIO(
            self.server.density_png(self.path).body)).convert("RGB")).astype(int)
        self.assertTrue((px[..., 0] == px[..., 1]).all()
                        and (px[..., 1] == px[..., 2]).all(), "must be gray")
        self.assertEqual(int(px.max()), 255, "the densest cells render white")
        self.assertEqual(int(px.min()), 0, "empty plane renders black")

    def test_density_png_and_the_figure_fallback_are_one_image(self):
        """The result figure's no-DAPI background (figures.plane_density) is
        exactly what curation showed, so the two pages cannot disagree."""
        import io
        import numpy as np
        from PIL import Image
        from allen_alignment.figures import plane_density
        png = np.asarray(Image.open(io.BytesIO(
            self.server.density_png(self.path).body)).convert("L")).astype(int)
        d = plane_density(self.server._load(self.server._resolve(self.path)))
        from matplotlib import cm
        want = (cm.gray(d) * 255).astype(np.uint8)[..., 0]
        self.assertEqual(png.shape, d.shape)
        self.assertTrue((png == want).all())

    def test_marker_png_renders_when_advertised(self):
        m = self.server.bundle_meta(self.path)
        if not m["has_markers"]:
            self.skipTest("panel does not support the marker background")
        resp = self.server.marker_density_png(self.path)
        self.assertEqual(resp.media_type, "image/png")
        self.assertGreater(len(resp.body), 1000)
        self.assertEqual(resp.body[:8], b"\x89PNG\r\n\x1a\n")

    def test_marker_png_honours_the_region_selection(self):
        """The legend toggles reach the renderer, and the cache keys on them."""
        m = self.server.bundle_meta(self.path)
        if not m["has_markers"]:
            self.skipTest("panel does not support the marker background")
        used = [r["acronym"] for r in
                self.server.markers_report(self.path)["regions"] if r["used"]]
        if len(used) < 2:
            self.skipTest("need two divisions to tell the renders apart")
        every = self.server.marker_density_png(self.path).body
        one = self.server.marker_density_png(self.path, regions=used[0]).body
        self.assertNotEqual(every, one, "a subset must render differently")
        # same selection twice must be byte-identical (it is served from cache)
        again = self.server.marker_density_png(self.path, regions=used[0]).body
        self.assertEqual(one, again)
        # whitespace and ordering in the parameter must not defeat the cache
        spaced = self.server.marker_density_png(
            self.path, regions=f" {used[0]} ,").body
        self.assertEqual(one, spaced)

    def test_density_png_renders(self):
        resp = self.server.density_png(self.path)
        self.assertEqual(resp.media_type, "image/png")
        self.assertEqual(resp.body[:8], b"\x89PNG\r\n\x1a\n")

    def test_unknown_bundle_is_404_not_a_crash(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            self.server.bundle_meta("/nonexistent/manual_bundle_nope.npz")
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(_REPORT_JOB, "no completed job whose panel zip repeats its "
                                  "combined figure")
class TestReportArchive(unittest.TestCase):
    """The master report must not store the same file twice.

    The combined result figure reaches the archive by two routes: directly, as
    `fig_path`, and again inside the per-panel zip, which is unpacked into the
    SAME folder. A zip may legally hold two entries under one name and most
    tools then extract one and drop the other without a word, so this was not
    visibly broken -- it was 7 MB of silent duplication per view, 14.5 MB on a
    78 MB archive.
    """

    @classmethod
    def setUpClass(cls):
        import io
        import zipfile
        from web import server
        resp = server.job_report_zip(_REPORT_JOB)
        cls.zip = zipfile.ZipFile(io.BytesIO(resp.body))
        cls.names = cls.zip.namelist()

    def test_the_archive_is_not_empty(self):
        """Guard the guard: an empty archive trivially has no duplicates."""
        self.assertGreater(len(self.names), 5)

    def test_no_entry_is_stored_twice(self):
        import collections
        dup = sorted(n for n, c in collections.Counter(self.names).items() if c > 1)
        self.assertEqual(dup, [], f"stored twice: {dup}")

    def test_the_combined_figure_is_present_exactly_once(self):
        """Deduplicating by dropping it altogether would also pass the test
        above, and would be worse than the bug."""
        combined = [n for n in self.names
                    if n.startswith("figures_curated/") and n.endswith(".png")
                    and "result" in os.path.basename(n)
                    and "cells_" not in n and "regions_over" not in n]
        self.assertEqual(len(combined), 1,
                         f"expected one combined curated figure, got {combined}")

    def test_the_manifest_lists_exactly_what_the_archive_holds(self):
        """It used to list only the directly-added files, omitting every
        unpacked panel -- so a reader checking the manifest against the folder
        would have found files they were never told about."""
        import json
        listed = set(json.loads(self.zip.read("manifest.json"))["contents"])
        held = set(self.names) - {"manifest.json"}
        self.assertEqual(listed - held, set(), "manifest lists absent files")
        self.assertEqual(held - listed, set(), "archive holds unlisted files")

    def test_every_entry_says_which_alignment_it_describes(self):
        """The archive mixes two alignments. A file that names neither is a file
        the reader has to guess about, and the guess is not always available:
        the automatic per-cell CSV differs from the curated one only by NOT
        having a "_curated" suffix."""
        import json
        d = json.loads(self.zip.read("manifest.json"))["describes"]
        missing = sorted(set(self.names) - {"manifest.json"} - set(d))
        self.assertEqual(missing, [], f"unlabelled: {missing}")
        self.assertEqual(set(d.values()) - {"curated", "auto"}, set())

    def test_the_two_per_cell_csvs_are_labelled_as_opposite_views(self):
        import json
        d = json.loads(self.zip.read("manifest.json"))["describes"]
        pc = {n: d[n] for n in self.names if n.startswith("per_cell/")}
        if len(pc) < 2:
            self.skipTest("this job has only one per-cell CSV")
        self.assertEqual(sorted(pc.values()), ["auto", "curated"],
                         f"per_cell/ should hold one of each view, got {pc}")

    def test_the_figure_folders_hold_the_same_panels(self):
        """Auto and curated are the same renderer over different alignments, so
        a panel present in one and missing from the other means a render failed
        silently rather than that the views legitimately differ."""
        import os as _os

        def kinds(folder, prefix):
            out = set()
            for n in self.names:
                if not n.startswith(folder + "/"):
                    continue
                stem, ext = _os.path.splitext(_os.path.basename(n))
                if stem.startswith(prefix):
                    stem = stem[len(prefix):]
                # drop the tag and the curated marker; what is left names the panel
                stem = stem.replace("_curated", "")
                out.add((stem.split("_tr_")[0].split("_region_")[0], ext))
            return out

        auto = kinds("figures_auto", "register_")
        cur = kinds("figures_curated", "curated_")
        if not auto or not cur:
            self.skipTest("this job has only one of the two figure sets")
        self.assertEqual(auto, cur,
                         f"panels differ: auto-only {sorted(auto - cur)}, "
                         f"curated-only {sorted(cur - auto)}")

    def test_single_view_folders_name_the_view_in_the_folder(self):
        """The region tables and the hex QC are computed from ONE per-cell CSV --
        curated when there is one, automatic otherwise -- and their filenames are
        keyed on the base tag, so they say neither. Without the folder naming it,
        two archives with identical file listings could describe different
        alignments."""
        import os as _os
        for stem in ("region_tables", "hex_bin_qc", "geometry"):
            bare = [n for n in self.names
                    if _os.path.dirname(n) == stem]
            self.assertEqual(bare, [],
                             f"{stem}/ does not say which alignment it "
                             f"describes; expected {stem}_curated/ or "
                             f"{stem}_auto/")


class DerivedExportNamingTests(unittest.TestCase):
    """Derived tables must be named after the CSV they were derived FROM.

    The region tables and the hex QC are computed from one per-cell CSV --
    curated when a job has one, automatic otherwise -- and land flat in
    `out_dir` keyed on a tag. They used to use the job's BASE tag, which says
    nothing about curation, so a curated job and an auto-only job on the SAME
    SECTION wrote different content to the same filename and whichever ran last
    won. Reproduced on a real section before the fix: all three region tables
    and both hex QC outputs differed in content under one name.

    The per-cell CSVs never had this problem, because finalize marks the curated
    one. These tests pin that the derived tables now carry the same marker.

    No file I/O: the defect was entirely in the name, so the naming rule is what
    is checked. Running the real exports takes minutes and would be testing
    pandas.
    """

    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server

    @staticmethod
    def _job(curated):
        j = {"id": "j1", "tag": "_tr_20240101_R1", "name": "tr_20240101_R1",
             "register": {"result": {"csv_path": "/out/auto.csv"}}}
        if curated:
            j["manual"] = {"result": {"csv_path": "/out/curated.csv"}}
        return j

    def test_a_curated_job_and_an_auto_job_on_one_section_get_different_tags(self):
        """The regression itself."""
        _, cur_tag, cur_view = self.server._derived_source(self._job(True))
        _, aut_tag, aut_view = self.server._derived_source(self._job(False))
        self.assertNotEqual(cur_tag, aut_tag,
                            "both views still name their derived tables "
                            f"{cur_tag!r}; they will overwrite each other")
        self.assertEqual((cur_view, aut_view), ("curated", "auto"))

    def test_the_tag_carries_the_same_marker_the_source_csv_does(self):
        """Consistency with finalize: the curated per-cell CSV is marked
        `_curated`, so its derived tables must be too. A different marker would
        be just as unique and much harder to match up by eye."""
        _, cur_tag, _ = self.server._derived_source(self._job(True))
        _, aut_tag, _ = self.server._derived_source(self._job(False))
        self.assertEqual(aut_tag, "tr_20240101_R1")
        self.assertEqual(cur_tag, "tr_20240101_R1_curated")

    def test_the_returned_csv_matches_the_view(self):
        """Naming it curated while deriving it from the automatic CSV would be
        worse than the original bug: the name would assert something false."""
        csv, _, view = self.server._derived_source(self._job(True))
        self.assertEqual((csv, view), ("/out/curated.csv", "curated"))
        csv, _, view = self.server._derived_source(self._job(False))
        self.assertEqual((csv, view), ("/out/auto.csv", "auto"))

    def test_no_csv_at_all_is_reported_rather_than_guessed(self):
        j = {"id": "j1", "tag": "_t", "name": "t"}
        csv, tag, view = self.server._derived_source(j)
        self.assertIsNone(csv)
        self.assertEqual(view, "auto")

    def test_an_untagged_job_falls_back_to_its_name(self):
        j = {"id": "j1", "tag": "", "name": "fallback",
             "register": {"result": {"csv_path": "/out/a.csv"}}}
        self.assertEqual(self.server._derived_source(j)[1], "fallback")

    def test_both_endpoints_and_the_report_use_the_one_rule(self):
        """Three call sites computed this independently before. A second copy is
        how the two halves drift apart again."""
        import inspect
        src = inspect.getsource(self.server)
        self.assertEqual(src.count("_derived_source(j)"), 3,
                         "expected region_exports, hex_qc and report.zip to all "
                         "go through _derived_source")
        self.assertNotIn('"curated" if man.get("csv_path") else "auto"', src,
                         "a second copy of the view rule has reappeared")
