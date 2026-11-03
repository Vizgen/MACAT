"""Structural guards for the interactive panels' controls.

Source-level rather than by rendering, because rendering these needs a browser:
they are React components drawing to a canvas. What is checked here is not how
they look but two properties a later edit could silently drop, both of which
cost the reader something real:

  1. every interactive panel has a way back to the whole section. Zooming in is
     one scroll; zooming back out to exactly the section is not, and there is no
     other route home.
  2. the marker panel's two pill rows stay told apart. One hides an ATLAS REGION
     from the overlay, the other drops a MARKER DIVISION from the background
     image. The pills are deliberately styled alike -- coloured swatch, struck
     through when off -- so as bare adjacent rows they read as one control that
     happened to wrap, and clicking the wrong row changes a different layer than
     the reader meant.
"""
import os
import re
import unittest

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_STUDY, "web", "frontend", "src")

# Every component that draws a pannable/zoomable canvas the reader can get lost in.
PANNABLE = ["CellCanvas.jsx", "OverlayCanvas.jsx"]


def _read(*parts):
    with open(os.path.join(_SRC, *parts)) as fh:
        return fh.read()


class ResetViewTests(unittest.TestCase):

    def test_every_pannable_panel_has_a_reset_control(self):
        for name in PANNABLE:
            src = _read(name)
            self.assertIn("reset view", src,
                          f"{name} pans and zooms but offers no way back to the "
                          f"whole section")

    def test_the_reset_control_calls_the_fit_function(self):
        """A button labelled 'reset view' that is not wired to fit() would look
        right and do nothing."""
        for name in PANNABLE:
            src = _read(name)
            self.assertRegex(
                src, r"onClick=\{fit\}",
                f"{name}'s reset button is not wired to fit()")

    def test_the_panels_still_pan_and_zoom(self):
        """Guard the guard: if a panel stops being interactive the tests above
        would be asserting something pointless."""
        for name in PANNABLE:
            src = _read(name)
            self.assertIn("onPointerMove", src, f"{name} no longer pans")
            self.assertIn('addEventListener("wheel"', src,
                          f"{name} no longer zooms")


class PillSectionTests(unittest.TestCase):

    def test_overlay_canvas_exports_the_section_wrapper(self):
        src = _read("OverlayCanvas.jsx")
        self.assertIn("export { PillSection }", src,
                      "the section wrapper is not exported, so ResultsStep "
                      "cannot label the background pills the same way")

    def test_region_pills_are_wrapped_and_named_as_the_overlay(self):
        src = _read("OverlayCanvas.jsx")
        self.assertRegex(src, r'<PillSection\s+layer="Overlay"',
                         "the region pills are not labelled as acting on the overlay")

    def test_marker_division_pills_are_wrapped_and_named_as_the_background(self):
        src = _read("steps", "ResultsStep.jsx")
        self.assertRegex(src, r'<PillSection\s+layer="Background"',
                         "the marker division pills are not labelled as acting "
                         "on the background image")

    def test_the_two_groups_carry_different_layer_names(self):
        """The whole point is that the two rows are distinguishable. Identical
        labels would be worse than none: they would assert sameness."""
        layers = set()
        for path in [("OverlayCanvas.jsx",), ("steps", "ResultsStep.jsx")]:
            layers.update(re.findall(r'<PillSection\s+layer="([^"]+)"', _read(*path)))
        self.assertEqual(layers, {"Overlay", "Background"},
                         f"expected exactly one Overlay and one Background "
                         f"pill group, found {sorted(layers)}")

    def test_the_overlay_group_comes_before_the_background_group(self):
        """Overlay first, in both panels.

        Two things depend on the order. The DAPI panel has no background pills,
        so whichever group comes first is the one that lines up across the pair
        -- put background first and the two panels start with different
        sections at different heights. And the stack should read the way the
        drawing is built: overlay on top of background.

        `extraControls` is where the background group is injected, so its
        position relative to the overlay `PillSection` IS the order on screen.
        """
        src = _read("OverlayCanvas.jsx")
        # the injection point, not the prop declaration in the signature
        slot = src.index("{extraControls}")
        overlay = src.index('<PillSection layer="Overlay"')
        self.assertLess(overlay, slot,
                        "the background pills are rendered before the overlay "
                        "pills; the DAPI and marker panels will not line up, and "
                        "the stack reads bottom layer first")

    def test_each_section_is_separated_by_a_rule(self):
        src = _read("OverlayCanvas.jsx")
        self.assertIn("borderTop", src,
                      "PillSection draws no rule, so the groups run together")


if __name__ == "__main__":
    unittest.main()


class CuratedSuffixTests(unittest.TestCase):
    """The "_curated" marker must be applied exactly once.

    `web/jobs.py` builds the finalize tag as the job tag plus "_curated", and
    `allen_alignment/finalize.py` then used it in "...{tag}_curated.csv" -- so
    every curated per-cell CSV and region GeoJSON was written as
    "..._curated_curated.csv". Harmless to a machine, but these are the files a
    reader is handed, and the doubled word says the pipeline does not know what
    it named its own output. The figures never had it, because they use the tag
    alone, which is what makes the two halves disagree.
    """

    def _finalize_src(self):
        with open(os.path.join(_STUDY, "allen_alignment", "finalize.py")) as fh:
            return fh.read()

    def test_jobs_still_bakes_curated_into_the_tag(self):
        """The premise. If this stops being true, the assertion below flips from
        'do not repeat it' to 'the marker is missing entirely'."""
        with open(os.path.join(_STUDY, "web", "jobs.py")) as fh:
            self.assertIn('job["tag"] + "_curated"', fh.read(),
                          "jobs.py no longer puts _curated in the tag; "
                          "finalize.py's filenames need rechecking")

    def test_finalize_does_not_append_curated_again(self):
        src = self._finalize_src()
        for stem, ext in (("cell_metadata_with_allen_regions", "csv"),
                          ("mapped_region_polygons", "geojson")):
            self.assertIn(f'f"{stem}{{tag}}.{ext}"', src,
                          f"the {ext} name is not '{stem}{{tag}}.{ext}'")
            self.assertNotIn(f'f"{stem}{{tag}}_curated.{ext}"', src,
                             f"{stem} appends _curated to a tag that already "
                             f"ends in it, giving ..._curated_curated.{ext}")


class ResultsSectionTests(unittest.TestCase):
    """The Results step is three sections, not eight peer headings.

    Every heading on the step used to be an `h3`, so the reader had to infer
    which ones belonged together. The grouping is now explicit: three
    `h2.section-title` sections, each holding its own `h3` subsections. A later
    edit that adds a heading at the wrong tier flattens it back without looking
    broken, which is what these pin.

    Headings are located by parsing the TAGS, not by searching for the words.
    The first attempt searched the file for "Check the boundaries" and matched
    the sentence in a comment that happened to name it.
    """

    # (text in the heading, which section it belongs under), in page order
    SUBSECTIONS = [("Check the boundaries", 0), ("Explore cells", 0),
                   ("Region composition", 1), ("Region specificity", 1),
                   ("Per-region agreement", 1), ("Spatial hex-bin QC", 1)]
    SECTION_TEXT = ["Final curated visualization", "Stats and QC",
                    "Job deliverables for downstream analysis"]

    def setUp(self):
        import re
        with open(os.path.join(_SRC, "steps", "ResultsStep.jsx")) as fh:
            self.src = fh.read()
        self.sections = [m.start() for m in
                         re.finditer(r'className="section-title"', self.src)]
        self.h3s = [(m.start(), self.src[m.end():m.end() + 240])
                    for m in re.finditer(r"<h3\b[^>]*>", self.src)]

    def _h3_pos(self, name):
        hits = [pos for pos, body in self.h3s if name in body]
        self.assertEqual(len(hits), 1,
                         f"expected exactly one h3 containing {name!r}, "
                         f"found {len(hits)}")
        return hits[0]

    def _heading_text(self, start):
        """Just the heading element. A fixed-width window instead ran past
        </h2> into the following comment and read its words as the title."""
        end = self.src.index("</h2>", start)
        return self.src[start:end]

    def _section_of(self, pos):
        before = [k for k, start in enumerate(self.sections) if start < pos]
        self.assertTrue(before, "this heading precedes every section title")
        return before[-1]

    def test_there_are_exactly_three_sections(self):
        self.assertEqual(len(self.sections), 3,
                         f"expected three section titles, found {len(self.sections)}")

    def test_each_section_title_says_what_it_says(self):
        for start, text in zip(self.sections, self.SECTION_TEXT):
            self.assertIn(text, self._heading_text(start),
                          f"the section at offset {start} does not read {text!r}")

    def test_the_auto_view_retitles_only_the_first_section(self):
        """Toggling Auto retitles section 1; a stale 'Final curated' heading
        over automatic panels would misattribute the whole section."""
        self.assertIn("Automatic alignment visualization",
                      self._heading_text(self.sections[0]))

    def test_every_subsection_sits_under_the_right_section(self):
        for name, want in self.SUBSECTIONS:
            got = self._section_of(self._h3_pos(name))
            self.assertEqual(got, want,
                             f"{name!r} renders under {self.SECTION_TEXT[got]!r}, "
                             f"expected {self.SECTION_TEXT[want]!r}")

    def test_the_subsections_are_in_page_order(self):
        seen = [self._h3_pos(n) for n, _ in self.SUBSECTIONS]
        self.assertEqual(seen, sorted(seen), "subsections are out of page order")

    def test_no_subsection_was_promoted_to_the_section_tier(self):
        """A subsection carrying section-title looks almost right and destroys
        the grouping: the page would show four or more top-level sections and
        the reader would lose the three-way split.

        Asserting "the h3 I just found starts with <h3" would be vacuous -- the
        regex that found it already required that. The real property is that no
        subsection name appears inside a SECTION heading."""
        for start in self.sections:
            head = self._heading_text(start)
            for name, _ in self.SUBSECTIONS:
                self.assertNotIn(name, head,
                                 f"{name!r} appears in a section title; it is a "
                                 f"subsection and belongs in an h3")

    def test_the_section_tier_outranks_the_subsection_tier(self):
        import re
        with open(os.path.join(_SRC, "styles.css")) as fh:
            css = fh.read()
        sec = re.search(r"h2\.section-title\s*\{[^}]*font-size:\s*([\d.]+)em", css)
        sub = re.search(r"^h3\s*\{[^}]*font-size:\s*([\d.]+)em", css, re.M)
        self.assertIsNotNone(sec, "h2.section-title has no em font-size")
        self.assertIsNotNone(sub, "h3 has no em font-size")
        self.assertGreater(float(sec.group(1)), float(sub.group(1)),
                           "section titles are not larger than subsection titles")

    def test_the_first_section_does_not_draw_a_rule_above_itself(self):
        """The rule separates sections; above the first it just underlines the
        step header. Keyed on an explicit class: `:first-of-type` keys on
        sibling position, not on which .section-head comes first, so it would
        match or miss depending on surrounding markup."""
        with open(os.path.join(_SRC, "styles.css")) as fh:
            css = fh.read()
        self.assertIn(".section-head.first", css)
        self.assertNotIn(".section-head:first-of-type", css)
        self.assertIn('className="row section-head first"', self.src)


class ResultsColumnWidthTests(unittest.TestCase):
    """The step has two content widths, and only two.

    It used to set a different maxWidth per block -- 620, 640, 660, 700, 900 --
    so nothing lined up down the page and the right edge was ragged. Two named
    classes replaced them:

        .col-wide   the interactive panels, ~2/3 of the page. NOT full width on
                    purpose: hovering a panel locks page scroll, because the
                    canvas takes the wheel event so zooming does not scroll the
                    page. The reader needs a strip of plain background beside it
                    to put the cursor over and scroll normally.
        .col-half   every table, figure and download block, matching the width
                    Region composition already used.

    An inline maxWidth added back to one of those blocks would look fine on the
    author's monitor and break the alignment on everyone else's.
    """

    def setUp(self):
        with open(os.path.join(_SRC, "steps", "ResultsStep.jsx")) as fh:
            self.jsx = fh.read()
        with open(os.path.join(_SRC, "styles.css")) as fh:
            self.css = fh.read()

    def test_both_width_classes_exist_and_wide_is_wider(self):
        import re
        w = re.search(r"\.col-wide\s*\{[^}]*?(\d+)%\s*\)", self.css)
        h = re.search(r"\.col-half\s*\{[^}]*?(\d+)%\s*\)", self.css)
        self.assertIsNotNone(w, ".col-wide is not defined with a percentage")
        self.assertIsNotNone(h, ".col-half is not defined with a percentage")
        self.assertGreater(int(w.group(1)), int(h.group(1)),
                           ".col-wide is not wider than .col-half")
        self.assertLess(int(w.group(1)), 100,
                        "the interactive panels span the full page; hovering one "
                        "locks page scroll, so there must be background beside it")

    def test_both_interactive_pairs_use_the_wide_column(self):
        """The overlay pair and the cell pair, one each."""
        self.assertEqual(self.jsx.count('className="row col-wide"'), 2,
                         "expected exactly two .col-wide panel rows")

    def test_two_panels_still_fit_side_by_side_in_the_wide_column(self):
        """A flex-basis wider than half the column silently stacks the pair,
        which is the layout the side-by-side comparison exists to avoid.

        The gap is READ from the source, not hardcoded. It was written as 16
        here while the page used 32, so the check was passing against a number
        the page no longer used -- exactly the drift it exists to catch."""
        import re
        floor = int(re.search(r"\.col-wide\s*\{[^}]*?max\((\d+)px", self.css).group(1))
        gaps = {int(m) for m in
                re.findall(r'className="row col-wide" style=\{\{ gap: (\d+)', self.jsx)}
        self.assertTrue(gaps, "no gap found on the wide panel rows")
        self.assertEqual(len(gaps), 1,
                         f"the two panel rows use different gaps: {sorted(gaps)}")
        gap = gaps.pop()
        bases = {int(m) for m in re.findall(r'flex: "1 1 (\d+)px"', self.jsx)}
        self.assertTrue(bases, "no flex basis found for the panels")
        widest = max(bases)
        self.assertLessEqual(widest * 2 + gap, floor,
                             f"two {widest}px panels plus the {gap}px gap do not "
                             f"fit in a {floor}px column, so they will stack")

    def test_the_later_sections_share_one_column(self):
        self.assertIn('<div className="col-half">', self.jsx)
        self.assertIn('className="dl-grid col-half"', self.jsx)

    def test_no_ad_hoc_widths_remain_in_the_shared_column(self):
        """Region specificity through the hex-bin QC. The caption in section 1
        keeps its own measure -- it is prose, and is outside this range."""
        start = self.jsx.index("4 — Region specificity")
        end = self.jsx.index("SECTION 3 — Job deliverables")
        self.assertNotIn("maxWidth", self.jsx[start:end],
                         "a block between Region specificity and the hex-bin QC "
                         "sets its own maxWidth again; it will not line up with "
                         "the rest of the column")


class RailStepLabelTests(unittest.TestCase):
    """The sidebar rail must not name an axis.

    Step 3 slides the atlas along whichever axis the section plane fixes: AP for
    coronal, ML for sagittal. The PANEL says so correctly -- `APStep` renders
    `{axis} level` from the plane metadata -- but the rail hardcoded "AP level",
    so on every sagittal job the rail and the panel it pointed at disagreed.

    The rail is a table of contents; it has no plane to consult and should not
    assert one.
    """

    def setUp(self):
        with open(os.path.join(_SRC, "App.jsx")) as fh:
            self.app = fh.read()

    def test_the_rail_step_is_plane_neutral(self):
        import re
        m = re.search(r"^const STEPS = \[(.*?)\];", self.app, re.M | re.S)
        self.assertIsNotNone(m, "the rail's STEPS array is gone")
        labels = re.findall(r'"([^"]+)"', m.group(1))
        self.assertEqual(len(labels), 6, f"expected six rail steps, got {labels}")
        self.assertEqual(labels[2], "Reference level")
        for bad in ("AP", "ML"):
            self.assertNotIn(bad, labels[2],
                             f"the rail step names the {bad} axis; it is shown "
                             f"for every plane")

    def test_the_panel_still_names_the_actual_axis(self):
        """Neutral in the rail, specific in the panel. Making the panel neutral
        too would lose the one place that tells you which way you are sliding."""
        with open(os.path.join(_SRC, "steps", "APStep.jsx")) as fh:
            ap = fh.read()
        self.assertIn("{axis} level", ap,
                      "the step-3 panel no longer names the axis it slides")

    def test_the_unused_label_list_agrees_with_the_rail(self):
        """jobapi exports a second copy of these labels that nothing renders
        today. Two lists of the same labels that disagree is how a stale one
        reaches the screen the day someone imports it."""
        import re
        with open(os.path.join(_SRC, "jobapi.js")) as fh:
            api = fh.read()
        m = re.search(r"export const STEPS = \[(.*?)\];", api, re.S)
        self.assertIsNotNone(m)
        labels = re.findall(r'label: "([^"]+)"', m.group(1))
        rail = re.findall(
            r'"([^"]+)"',
            re.search(r"^const STEPS = \[(.*?)\];", self.app, re.M | re.S).group(1))
        self.assertEqual(labels, rail,
                         "jobapi's STEPS labels have drifted from the rail's")
