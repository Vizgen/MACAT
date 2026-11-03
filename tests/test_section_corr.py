"""Whole-section expression correlation against the Allen reference.

This is the tissue-level QC that sits above Region specificity in step 6: does
the section look like brain at the AP it was registered to. It uses no region
labels and no alignment -- only the panel counts -- which is what makes it an
independent check rather than a restatement of the registration.

What these pin:
  * the shipped section reference keeps the columns the endpoint reads;
  * the correlation is the SKILL'S method (log10 Pearson over shared symbols),
    reimplemented here so a change to either side shows up as a disagreement
    rather than as a silently different number;
  * a coronal job is compared against ONE AP-matched section and a sagittal job
    against all of them pooled -- getting that backwards would score a sagittal
    slice against a single coronal level it only partly contains;
  * the unavailable paths say WHY, since this loads with the page and a blank
    panel would otherwise look like a bug.
"""
import os
import sys
import unittest

_pre = os.path.join(sys.prefix, "lib", "libstdc++.so.6")
if os.path.exists(_pre) and _pre not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = _pre + (
        ":" + os.environ["LD_PRELOAD"] if os.environ.get("LD_PRELOAD") else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)

import numpy as np  # noqa: E402

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import config  # noqa: E402

_REF = os.path.join(config.REFERENCE_DATA_DIR, "allen_abc_section_by_gene.csv")
_REF_SAG = os.path.join(config.REFERENCE_DATA_DIR,
                        "allen_abc_sagittal_by_gene.csv")


def skill_log10_pearson(x, y, eps=1e-4):
    """The transcript-QC method, written out.

    `cross_experiment_correlation` in the spatial-transcriptomics skill is
    log10 Pearson over shared gene symbols with a 1e-4 pseudocount. Duplicated
    rather than imported: the skill lives outside this repo, so importing it
    would make this suite fail on a checkout that does not have it -- and a copy
    that has drifted is exactly what this is meant to catch.
    """
    return float(np.corrcoef(np.log10(np.asarray(x, float) + eps),
                             np.log10(np.asarray(y, float) + eps))[0, 1])


@unittest.skipUnless(os.path.exists(_REF), "section reference not installed")
class SectionReferenceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import pandas as pd
        cls.df = pd.read_csv(_REF, index_col=0)

    def test_it_carries_the_columns_the_endpoint_reads(self):
        for col in ("ap_mm", "n_cells"):
            self.assertIn(col, self.df.columns,
                          f"the section reference lost its {col!r} column")

    def test_sections_are_ordered_and_span_the_atlas(self):
        ap = self.df["ap_mm"].to_numpy()
        self.assertTrue(np.all(np.diff(ap) > 0), "sections are not AP-ordered")
        span = 13.2                 # AP extent of the Allen CCF, mm, any resolution
        self.assertGreater(ap.max() - ap.min(), 0.5 * span,
                           "the reference covers less than half the atlas")
        self.assertLess(ap.max(), span + 1.0, "a section sits beyond the atlas")

    def test_the_profiles_are_mean_counts_not_log(self):
        """Same trap as the region reference: a log-transformed table would
        correlate against log10-transformed query counts and be wrong twice."""
        g = self.df.drop(columns=["ap_mm", "n_cells"])
        self.assertGreater(float(g.to_numpy().max()), 12.0,
                           "no gene exceeds 12 counts/cell; this looks logged")
        self.assertGreaterEqual(float(g.to_numpy().min()), 0.0)


class CorrelationMethodTests(unittest.TestCase):

    def test_a_modest_scale_factor_barely_moves_r(self):
        """In log space a global factor is a constant shift, so r is scale-free
        -- ALMOST. The 1e-4 pseudocount breaks it: log10(7x + eps) is not
        log10(7x) once x approaches the floor. A 7x change moves r by ~1e-6,
        which is why comparing two profiles on similar scales is safe."""
        rng = np.random.default_rng(0)
        x = rng.gamma(2.0, 1.0, 300)
        y = x * rng.uniform(0.8, 1.25, 300)
        self.assertAlmostEqual(skill_log10_pearson(x, y),
                               skill_log10_pearson(x, y * 7.0), places=4)

    def test_a_HUGE_scale_factor_does_move_r(self):
        """And this is why the endpoint puts BOTH sides on mean counts per cell.

        Totals against means differ by the cell count -- five orders of
        magnitude. At that ratio the pseudocount floors one profile's small
        genes and not the other's, so r changes for a purely clerical reason.
        The guard is that both sides are normalised the same way, not that log
        makes scale irrelevant."""
        rng = np.random.default_rng(3)
        x = rng.gamma(0.4, 0.05, 400)          # many genes near the floor
        y = x * rng.uniform(0.8, 1.25, 400)
        near = skill_log10_pearson(x, y)
        far = skill_log10_pearson(x, y * 1e5)
        self.assertGreater(abs(near - far), 1e-3,
                           "the pseudocount is not biting; pick a harsher case")

    def test_identical_profiles_correlate_at_one(self):
        x = np.random.default_rng(1).gamma(2.0, 1.0, 200)
        self.assertAlmostEqual(skill_log10_pearson(x, x), 1.0, places=9)

    def test_a_gene_absent_from_one_profile_stays_finite(self):
        """The pseudocount is what makes a zero plottable and correlatable;
        without it a single absent gene makes r NaN and the panel goes blank."""
        x = np.array([0.0, 1.0, 5.0, 0.2])
        y = np.array([0.1, 1.2, 4.0, 0.0])
        self.assertTrue(np.isfinite(skill_log10_pearson(x, y)))


@unittest.skipUnless(os.path.exists(_REF), "section reference not installed")
class EndpointTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server

    def test_a_job_without_a_confirmed_level_says_so(self):
        """It loads with the page, so a blank panel would read as a bug.

        The experiment folder has to be real for this: the endpoint checks the
        inputs it needs before the workflow state, so a fixture with no
        cell_by_gene.csv reports THAT instead and the test proves nothing.
        """
        import tempfile
        import web.jobs as jobs
        orig = jobs.load
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "cell_by_gene.csv"), "w").close()
            try:
                jobs.load = lambda jid: {"id": jid, "exp_path": d, "ap_idx": None}
                r = self.server.job_section_corr("x")
            finally:
                jobs.load = orig
        self.assertFalse(r["available"])
        self.assertIn("reference level", r["reason"])

    def test_a_missing_experiment_says_so(self):
        import web.jobs as jobs
        orig = jobs.load
        try:
            jobs.load = lambda jid: {"id": jid, "exp_path": "/nope", "ap_idx": 150}
            r = self.server.job_section_corr("x")
        finally:
            jobs.load = orig
        self.assertFalse(r["available"])
        self.assertIn("cell_by_gene", r["reason"])

    def test_the_ap_conversion_is_index_times_resolution(self):
        """ap_idx counts atlas slices from the ANTERIOR pole; the ABC reference
        is in the same mm. Getting this wrong picks a reference section from the
        wrong end of the brain and still returns a plausible r."""
        # the index is converted at the resolution it was PICKED on, which for a
        # job made before 25 um was the default is 50 um, not the server's
        from web import jobs
        self.assertAlmostEqual(153 * jobs.ap_res_um({"ap_idx": 153}) / 1000.0,
                               7.65, places=6)
        self.assertAlmostEqual(
            306 * jobs.ap_res_um({"ap_idx": 306, "ap_res_um": 25.0}) / 1000.0,
            7.65, places=6)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.path.exists(_REF_SAG), "sagittal reference not installed")
class SagittalReferenceTests(unittest.TestCase):
    """The sagittal reference is a RE-SLICE of the same Allen point cloud.

    The release was acquired coronally, but every cell carries CCF coordinates,
    so a sagittal band is those same cells cut along ML instead. A sagittal
    query therefore gets a sagittal reference at its own ML, rather than a
    pooled average that corresponds to no section at all.
    """

    @classmethod
    def setUpClass(cls):
        import pandas as pd
        cls.df = pd.read_csv(_REF_SAG, index_col=0)
        cls.g = cls.df.drop(columns=["ml_mm", "n_cells"])

    def test_it_carries_an_ml_coordinate_not_an_ap_one(self):
        self.assertIn("ml_mm", self.df.columns)
        self.assertNotIn("ap_mm", self.df.columns,
                         "the sagittal table is keyed on AP; it was built from "
                         "the wrong axis")

    def test_bands_are_ordered_and_span_the_ML_axis(self):
        ml = self.df["ml_mm"].to_numpy()
        self.assertTrue(np.all(np.diff(ml) > 0), "bands are not ML-ordered")
        width = 11.4                # ML extent (atlas axis 2), mm, any resolution
        self.assertGreater(ml.max() - ml.min(), 0.5 * width)
        self.assertLess(ml.max(), width + 1.0, "a band sits beyond the atlas")

    def test_the_two_planes_share_the_gene_columns(self):
        """Both are cut from one point cloud, so a gene present in one and not
        the other would mean they came from different builds."""
        import pandas as pd
        cor = pd.read_csv(_REF, index_col=0, nrows=1)
        a = set(cor.columns) - {"ap_mm", "n_cells"}
        b = set(self.g.columns)
        self.assertEqual(a, b, "the coronal and sagittal panels differ")

    def test_mirrored_bands_agree_because_the_brain_is_symmetric(self):
        """The strongest available check that the ML binning is right: bands the
        same distance either side of the midline must look nearly identical.

        It is also the reason a LEFT/RIGHT FLIP of this axis would be almost
        undetectable here -- which is why the axis is taken from the atlas frame
        rather than inferred from the data.
        """
        ml = self.df["ml_mm"].to_numpy()
        mid = ml.min() + (ml.max() - ml.min()) / 2
        for off in (1.0, 2.0, 3.0):
            i = int(np.argmin(np.abs(ml - (mid - off))))
            j = int(np.argmin(np.abs(ml - (mid + off))))
            x = self.g.iloc[i].to_numpy(float)
            y = self.g.iloc[j].to_numpy(float)
            r = skill_log10_pearson(x, y)
            self.assertGreater(r, 0.98,
                               f"bands {ml[i]:.2f} and {ml[j]:.2f} mm correlate "
                               f"only at {r:.3f}; the ML binning looks wrong")


@unittest.skipUnless(os.path.exists(_REF_SAG), "sagittal reference not installed")
class PlaneRoutingTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server

    def test_each_plane_is_routed_to_its_own_reference(self):
        from web import server
        self.assertEqual(set(server.SECTION_REFS), {"coronal", "sagittal"})
        self.assertEqual(server.SECTION_REFS["coronal"][1], "ap_mm")
        self.assertEqual(server.SECTION_REFS["sagittal"][1], "ml_mm")
        self.assertEqual(server.SECTION_REFS["coronal"][2], "AP")
        self.assertEqual(server.SECTION_REFS["sagittal"][2], "ML")

    def test_an_unknown_plane_is_refused_rather_than_defaulted(self):
        """Defaulting to coronal would score a section against the wrong axis
        entirely and still return a plausible-looking r."""
        import web.jobs as jobs
        orig = jobs.load
        try:
            jobs.load = lambda jid: {"id": jid, "exp_path": _STUDY, "ap_idx": 100,
                                     "orientation": {"plane": "horizontal"}}
            r = self.server.job_section_corr("x")
        finally:
            jobs.load = orig
        self.assertFalse(r["available"])
        self.assertIn("horizontal", r["reason"])
