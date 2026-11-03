"""The two resolution knobs, and why they must agree.

ATLAS_RES_UM sets the brainglobe atlas grid; PLANE_DX_UM sets the J-plane the
registration rasterises onto. Every cell's Allen label is
``AphiL[round(y / drow), round(x / dcol)]``, so PLANE_DX_UM is what quantises the
deliverable, while ATLAS_RES_UM is what the annotation is read from.

Measured on tr_202401020900_R1 (2026-09-21), same section and same physical AP:

    50/50   134x235 plane   166 regions   49.6% of cells on a boundary pixel
    25/25   268x470 plane   167 regions   29.4%

and the mismatched combination is NOT a halfway house -- it is wrong:

    50/25   268x470 plane    72 regions   tissue 100%, 3.9% label agreement

That last one reports **coverage 1.0** to the Results page, i.e. it fails in the
direction a reviewer is least likely to question. Hence the import-time guard,
which is what this file exists to pin.
"""
import os
import subprocess
import sys
import unittest

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import config                                        # noqa: E402


def _import_with(atlas_res, plane_dx):
    """Import config in a CLEAN process with those env values.

    A subprocess is not laziness: the guard runs at import time and `config` is
    already imported here, so re-importing in-process would not re-run it.
    """
    env = dict(os.environ, ALLEN_ATLAS_RES_UM=str(atlas_res),
               ALLEN_PLANE_DX_UM=str(plane_dx))
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import config; "
         "print(config.ATLAS_RES_UM, config.PLANE_DX_UM)" % _STUDY],
        capture_output=True, text=True, env=env, cwd=_STUDY)


class ResolutionConfigTests(unittest.TestCase):
    def test_defaults_are_twenty_five_and_match(self):
        """Switched from 50 to 25 deliberately in v0.4.0, once a 50 um bundle
        was refused rather than misread (test_resolution_switch.py). Pinned so
        the next change of default is just as deliberate. Checked in a clean
        process with both variables unset, so a run of the suite under a 50 um
        override still tests the SHIPPED default rather than the override."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("ALLEN_ATLAS_RES_UM", "ALLEN_PLANE_DX_UM")}
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import config; "
             "print(config.ATLAS_RES_UM, config.PLANE_DX_UM)" % _STUDY],
            capture_output=True, text=True, env=env, cwd=_STUDY)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["25.0", "25.0"])

    def test_matching_override_is_accepted(self):
        out = _import_with(25, 25)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["25.0", "25.0"])

    def test_mismatched_knobs_are_refused_at_import(self):
        """A coarse atlas on a fine plane rescales the silhouette fit and yields a
        registration that claims 100% coverage. Refuse it before it runs."""
        out = _import_with(50, 25)
        self.assertNotEqual(out.returncode, 0, "a mismatch must not be allowed")
        self.assertIn("must be equal", out.stderr)

    def test_the_refusal_explains_the_failure_mode(self):
        """A bare 'must be equal' invites someone to "fix" it by matching them to
        whichever value they did not want. Name the consequence."""
        err = _import_with(25, 50).stderr
        self.assertIn("100% coverage", err)


if __name__ == "__main__":
    unittest.main()
