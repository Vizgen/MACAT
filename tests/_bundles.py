"""Real registration bundles for the tests that need one.

Several suites borrow a bundle from the output store rather than synthesising
one, because what they test (boundary extraction, the warp extent, the server's
image endpoints) depends on real geometry. Two rules, both learned the hard way:

  * Only a bundle at the CONFIGURED resolution is usable. A 50 um bundle under a
    25 um server is refused by load_bundle, by design, so offering one here
    would make those suites fail for a reason that is not a bug. With none at
    the configured resolution, the suites skip and say why.
  * The store is ALLEN_OUT_ROOT when set, else pipeline_output -- the same rule
    the app follows (web/jobs.py) -- so a scratch store of fresh bundles can
    exercise these suites without touching production.

Not named test_*.py, so discovery imports it only as a helper.
"""
import glob
import os
import sys

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

import numpy as np                                   # noqa: E402

import config                                        # noqa: E402
from allen_alignment.atlas import bundle_res_um      # noqa: E402

ROOT = os.path.abspath(os.environ.get("ALLEN_OUT_ROOT")
                       or os.path.join(_STUDY, "pipeline_output"))

SKIP_REASON = (f"no registration bundle at {config.PLANE_DX_UM:g} um in {ROOT}; "
               f"register a section at this resolution, or point ALLEN_OUT_ROOT "
               f"at a store that has one")


def find_bundles():
    """Bundle paths in the store at the configured resolution, sorted."""
    out = []
    paths = glob.glob(os.path.join(ROOT, "manual_bundle*.npz"))
    # per-job outputs (jobs/<id>/outputs/, since 2026-10-28)
    paths += glob.glob(os.path.join(ROOT, "jobs", "*", "outputs", "manual_bundle*.npz"))
    for p in sorted(paths):
        try:
            with np.load(p, allow_pickle=True) as d:
                if abs(bundle_res_um(d) - config.PLANE_DX_UM) < 1e-6:
                    out.append(p)
        except Exception:
            continue
    return out


def first_bundle():
    hits = find_bundles()
    return hits[0] if hits else None
