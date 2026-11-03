"""Shared configuration for the Allen mouse-brain region-mapping pipeline.

Single source of truth for the input region and output locations, imported by
every stage script (m1 / m3 / m4) so the paths are not duplicated. See notes.md
for the full design; §11 explains that the unit of processing is a REGION, not a
whole experiment folder.
"""
import os

# Repository layout. Runtime outputs stay at the repository root for backwards
# compatibility; versioned inputs are separated from code under data/.
STUDY_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(STUDY_DIR, "data")
REFERENCE_DATA_DIR = os.path.join(DATA_DIR, "reference")
METADATA_DIR = os.path.join(DATA_DIR, "metadata")

# MERFISH region directories: each contains cell_metadata.csv, cell_by_gene.csv
# and (for some) images/ (mosaic_DAPI*.tif, micron_to_mosaic_pixel_transform.csv).
# Select one at run time with the REGION env var, e.g. `REGION=<key> python ...`.
#
# NO REGION PATHS ARE HARD-CODED HERE. Acquisition directory names can carry
# sample and study identifiers, so they never belong in this repository. Two
# sources populate REGIONS at import:
#
#   1. A host-local JSON map, gitignored, at data/metadata/regions.local.json
#      (override the location with ALLEN_REGIONS_FILE). Either form works:
#
#          {"mykey": "/abs/path/to/region_R1"}
#          {"mykey": {"path": "/abs/path/to/region_R1", "transpose": true,
#                     "angle": -10, "flip": "x"}}
#
#      `transpose`/`angle`/`flip` are optional and feed _TRANSPOSE / _ORIENT.
#   2. Auto-discovery under TRAIN_BASE (below), minus EXCLUDED_REGIONS.
#
# A checkout with neither source present imports cleanly and simply offers no
# regions; the web app's Input step will list nothing until one is configured.
REGIONS = {}
_LOCAL_TRANSPOSE = {}
_LOCAL_ORIENT = {}


def _load_local_regions():
    """Read the host-local region map. Missing file -> no regions, no error."""
    import json
    p = (os.environ.get("ALLEN_REGIONS_FILE")
         or os.path.join(METADATA_DIR, "regions.local.json"))
    if not os.path.isfile(p):
        return
    with open(p) as fh:
        raw = json.load(fh)
    for key, val in raw.items():
        if isinstance(val, str):
            REGIONS[key] = val
            continue
        REGIONS[key] = val["path"]
        if "transpose" in val:
            _LOCAL_TRANSPOSE[key] = bool(val["transpose"])
        if val.get("angle") is not None:
            _LOCAL_ORIENT[key] = (float(val["angle"]), val.get("flip") or None)


_load_local_regions()


# Auto-discover a host's training experiments for the command-line QC and
# validation scripts, which address sections by key (`tr_<timestamp>_<Rn>`).
# The app does not use this since v0.5.0: step 1 lists sections from the data
# folder (web/datafolder.py). Off unless ALLEN_TRAIN_BASE names the folder;
# there is no built-in path, so no host's folder layout is in the code.
TRAIN_BASE = os.environ.get("ALLEN_TRAIN_BASE", "").strip()

# Section plane per experiment, read from a tracked document rather than guessed
# from a folder name. Auto-discovery scans ONE root and cannot tell a coronal
# experiment from a sagittal one by looking at it, and getting it wrong is silent:
# a sagittal section registered as coronal still yields a full, plausible set of
# labels. See data/metadata/section_planes.csv for the schema.
SECTION_PLANES_FILE = os.environ.get(
    "ALLEN_SECTION_PLANES") or os.path.join(METADATA_DIR, "section_planes.csv")

# key -> section plane, for keys the document assigns one. Absent means coronal.
REGION_PLANE = {}


def _load_section_planes():
    """[(match, plane)] from the document, longest match first.

    Longest-first so a full region key overrides the experiment prefix it sits
    under, which is the only way to express "this experiment is coronal except
    for one region".
    """
    rules = []
    if not os.path.isfile(SECTION_PLANES_FILE):
        return rules
    import csv as _csv
    with open(SECTION_PLANES_FILE) as fh:
        rows = _csv.DictReader(r for r in fh if r.strip() and not r.startswith("#"))
        for row in rows:
            m = (row.get("match") or "").strip()
            p = (row.get("plane") or "").strip().lower()
            if m and p:
                rules.append((m, p))
    return sorted(rules, key=lambda r: -len(r[0]))


SECTION_PLANE_RULES = _load_section_planes()


def _plane_for(key, experiment):
    """The declared plane for a region, or None meaning the coronal default."""
    for match, plane in SECTION_PLANE_RULES:
        if key == match or experiment.startswith(match):
            return plane
    return None


# Region keys to hide on this host. They are deliberately NOT listed here: a key
# can itself identify whose data it is. The list lives in a host-local,
# gitignored file (one region key per line, `#` comments allowed):
#
#     data/metadata/excluded_regions.txt
#
# or in the `ALLEN_EXCLUDED_REGIONS` environment variable (comma-separated).
# Auto-discovery skips these keys so withheld datasets do not reappear as
# selectable regions on a host where the source directories still exist.
def _load_excluded_regions():
    keys = set()
    p = os.path.join(METADATA_DIR, "excluded_regions.txt")
    if os.path.isfile(p):
        with open(p) as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    keys.add(line)
    keys.update(k.strip() for k in
                os.environ.get("ALLEN_EXCLUDED_REGIONS", "").split(",") if k.strip())
    return frozenset(keys)


EXCLUDED_REGIONS = _load_excluded_regions()


def _discover_training():
    """One root, one key scheme: `tr_<timestamp>_<Rn>`.

    The plane is looked up per experiment in SECTION_PLANES_FILE, so a dataset is
    added by dropping it under TRAIN_BASE and, if it is not coronal, writing one
    line in that document.
    """
    found = {}
    if not TRAIN_BASE or not os.path.isdir(TRAIN_BASE):
        return found
    for e in sorted(os.listdir(TRAIN_BASE)):
        ep = os.path.join(TRAIN_BASE, e)
        if not os.path.isdir(ep) or e.startswith("__"):
            continue
        ts = e.split("_")[0]
        regs = sorted(r for r in os.listdir(ep)
                      if r.startswith("region_") and os.path.isdir(os.path.join(ep, r)))
        for r in regs:
            key = f"tr_{ts}_{r.replace('region_', '')}"
            if key in EXCLUDED_REGIONS:
                continue
            found[key] = os.path.join(ep, r)
            plane = _plane_for(key, e)
            if plane:
                REGION_PLANE[key] = plane
    return found


TRAIN_REGIONS = _discover_training()
REGIONS.update(TRAIN_REGIONS)

# Positive controls: Zhuang-lab MERFISH sections whose correct coronal/sagittal
# level, orientation and per-cell regions are KNOWN (data/positive_control/,
# built by scripts/data/build_positive_control.py). Listed from the tracked
# manifest, and only once a section's folder has actually been built, so a
# checkout without the data simply shows none. The step-1 dropdown groups them
# under "Positive control".
POSITIVE_CONTROL_DIR = (os.environ.get("ALLEN_POSITIVE_CONTROL_DIR")
                        or os.path.join(DATA_DIR, "positive_control"))
POSITIVE_CONTROLS = {}
POSITIVE_CONTROL_ANSWERS = {}   # name -> its sections.csv row (the answer key)


def _discover_positive_controls():
    import csv as _csv
    manifest = os.path.join(POSITIVE_CONTROL_DIR, "sections.csv")
    if not os.path.isfile(manifest):
        return
    with open(manifest, newline="") as fh:
        for row in _csv.DictReader(fh):
            # <name>/region_R1/ since 2026-10-28; a build from before that is
            # flat, <name>/ itself
            base = os.path.join(POSITIVE_CONTROL_DIR, "sections", row["name"])
            path = os.path.join(base, "region_R1")
            if not os.path.isfile(os.path.join(path, "cell_metadata.csv")):
                path = base
            if os.path.isfile(os.path.join(path, "cell_metadata.csv")):
                POSITIVE_CONTROLS[row["name"]] = path
                POSITIVE_CONTROL_ANSWERS[row["name"]] = row
                REGION_PLANE[row["name"]] = row["plane"]


_discover_positive_controls()
REGIONS.update(POSITIVE_CONTROLS)

# Legacy default key. It has no built-in path any more: it resolves only if the
# host-local map defines it. Kept as the untagged-output key so historical
# filenames stay backward compatible (see TAG below).
LEGACY_DEFAULT_REGION = "sw234"
REGION = os.environ.get("REGION", LEGACY_DEFAULT_REGION)
EXP = REGIONS.get(REGION)   # None when unconfigured; use region_path() to fail loudly


def region_path(key=None):
    """Resolve a region key to its directory, with an actionable error.

    Distinguishes the three ways a key can be missing: deliberately withheld,
    not configured on this host, or simply unknown."""
    key = REGION if key is None else key
    if key in REGIONS:
        return REGIONS[key]
    if key in EXCLUDED_REGIONS:
        raise KeyError(
            f"region {key!r} is withheld on this host "
            f"(listed in {os.path.join(METADATA_DIR, 'excluded_regions.txt')} "
            f"or ALLEN_EXCLUDED_REGIONS)")
    raise KeyError(
        f"unknown region {key!r}. Region paths are not committed to this repo; "
        f"define it in {os.path.join(METADATA_DIR, 'regions.local.json')} "
        f"(or ALLEN_REGIONS_FILE), or place it under TRAIN_BASE for "
        f"auto-discovery.")
# Output/file suffix that keeps each region's runs side by side; "" for the
# legacy default key so its existing filenames stay backward compatible. When a
# SECTION is selected (multi-section region, via the driver), append _sK so the
# per-section outputs do not collide.
_SEC = os.environ.get("SECTION")
TAG = ("" if REGION == "sw234" else "_" + REGION) + (f"_s{_SEC}" if _SEC else "")

# In-plane orientation of the rasterized cell-density so it lands DORSAL-UP
# (cortex up, vertical midline) -- the convention DeepSlice + the 3D-to-slice
# registration expect. This depends on how the section was mounted on the slide,
# so it is a per-experiment property of the acquisition, not of the code: set
# `"transpose": true` on an entry in the host-local region map. Keyed per
# experiment so all its regions share it. prep / to_init / register must all
# apply the SAME choice or the DeepSlice init affine will not line up.
_TRANSPOSE = dict(_LOCAL_TRANSPOSE)
TRANSPOSE = _TRANSPOSE.get(REGION, False)   # test-set regions: default raw (untuned)

# Per-experiment manual orientation to dorsal-up, matching the clustering notebooks'
# `align_spatial()`: rotate (deg, +CCW) -> mirror flip -> translate to origin,
# BAKED INTO THE COORDINATES (not ax.invert_yaxis, which is display-only and was a
# source of orientation bugs). flip_axis: 'x' negates y, 'y' negates x, None no flip.
# Tune (angle, flip) per experiment against the density preview. Keys with an entry
# here use this instead of _TRANSPOSE. 
_ORIENT = {
    # Built-in orientations for named sections. None ship with this copy: set
    # them per host in the region map (`angle`/`flip`, see above) instead.
}


def _load_orient_overrides():
    """Merge interactive orientation picks (from the old orientation picker) on top
    of the defaults above, so orientation can be set/adjusted in the UI
    without editing this file. CSV: region_key, angle, flip ('', 'x', or 'y')."""
    import csv
    p = os.path.join(METADATA_DIR, "orient_picks.csv")
    if not os.path.exists(p):
        return
    with open(p) as f:
        for row in csv.DictReader(f):
            k = (row.get("region_key") or "").strip()
            a = (row.get("angle") or "").strip()
            if not k or a == "":
                continue
            fl = (row.get("flip") or "").strip() or None
            _ORIENT[k] = (float(a), fl)


_ORIENT.update(_LOCAL_ORIENT)
_load_orient_overrides()


def orient_apply(x, y, angle, flip):
    """Core (angle, flip) point transform, same math as the notebooks' align_spatial:
    rotate (deg, +CCW) -> mirror flip ('x' negates y, 'y' negates x) -> shift to
    origin. Single source of truth so tools (e.g. the orientation assistant) apply
    the EXACT transform the pipeline bakes in."""
    import numpy as np
    xr, yr = orient_rotflip(x, y, angle, flip)
    return xr - xr.min(), yr - yr.min()


def orient_rotflip(x, y, angle, flip):
    """Just the rotate + mirror-flip part of orient_apply, WITHOUT the final
    shift-to-origin. Needed when two point sets (e.g. cells and transcripts) must
    share ONE coordinate frame: shift both by the SAME (cell-derived) origin
    instead of each by its own min, which would misalign them by a few pixels."""
    import numpy as np
    xy = np.stack([np.asarray(x, float), np.asarray(y, float)], axis=1)
    if angle:
        t = np.deg2rad(angle)
        xy = xy.dot(np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]]))
    if flip == "y":
        xy[:, 0] *= -1
    elif flip == "x":
        xy[:, 1] *= -1
    return xy[:, 0], xy[:, 1]


def orient_xy(x, y, key=None):
    """Apply the per-experiment (angle, flip) orientation to point coordinates,
    same math as the notebooks' align_spatial. No-op if the key has no _ORIENT entry.
    Default key is ORIENT_KEY (REGION, plus the _sK suffix when a SECTION is set) so
    multi-section regions pick up their per-section orientation."""
    key = key if key is not None else ORIENT_KEY
    if key not in _ORIENT:
        return x, y
    angle, flip = _ORIENT[key]
    return orient_apply(x, y, angle, flip)


# Orientation lookup key: REGION, plus _sK when a SECTION is selected (so a
# multi-section region's per-section orientation is found).
ORIENT_KEY = REGION + (f"_s{_SEC}" if _SEC else "")
HAS_ORIENT = ORIENT_KEY in _ORIENT   # if True, prep should use orient_xy, not TRANSPOSE


# Allen atlas (brainglobe) resolution in microns, and the spacing of the J-plane
# grid the registration rasterises onto. They are SEPARATE knobs and both must
# move for a resolution change to buy anything:
#
#   * ATLAS_RES_UM alone -- a finer annotation sampled onto the same plane boxes.
#     Per-cell labels stay quantised at PLANE_DX_UM, so almost no gain.
#   * PLANE_DX_UM alone -- a 50 um annotation interpolated `mode="nearest"` onto a
#     finer grid. The boundaries stay 50 um staircases drawn with more vertices.
#
# Why it matters: every cell's Allen label is `AphiL[round(y/drow), round(x/dcol)]`,
# so the label is decided by which PLANE_DX_UM box the centroid fell in. Measured
# on one test section at 50 um, 47.9% of labelled cells sit in a box that touches
# a different region; the fraction is close to linear in box width (74.1% at 100 um,
# 94.0% at 200 um), so halving the box roughly halves it.
#
# Default 25 um since v0.4.0 (2026-10-07): at-risk cells 49.6% -> 29.4% for
# +0.5 s of registration (measured 2026-09-21). Jobs registered at 50 um are
# refused, not reinterpreted -- see allen_alignment.atlas.check_bundle_resolution
# -- and carry their slice over by microns when re-picked. Setting both variables
# to 50 brings the old behaviour back, and the 50 um jobs with it.
#
# Overridable so a run can be compared against another without editing the file.
# AP INDEX IS RESOLUTION-DEPENDENT: index 150 at 50 um is index 300 at 25 um. Use
# `selected_ap_um` from a bundle, which is resolution-independent, to carry an AP
# choice across a change here.
ATLAS_RES_UM = float(os.environ.get("ALLEN_ATLAS_RES_UM", 25.0))
PLANE_DX_UM = float(os.environ.get("ALLEN_PLANE_DX_UM", 25.0))

# They must MATCH. Measured 2026-09-21: a 50 um atlas on a 25 um plane produced
# tissue covering 100% of the raster, only 72 leaf regions instead of 166, and
# 3.9% per-cell label agreement with the matched 50/50 run -- silently wrong, and
# wrong in the direction that LOOKS good, because the Results page would report
# 100.0% labelled. The silhouette initialisation derives its scale from the atlas
# grid and is applied on the plane grid, so a mismatch rescales the whole fit.
# Refuse it loudly rather than let a run like that reach a reviewer.
if abs(ATLAS_RES_UM - PLANE_DX_UM) > 1e-9:
    raise ValueError(
        f"ALLEN_ATLAS_RES_UM ({ATLAS_RES_UM}) and ALLEN_PLANE_DX_UM "
        f"({PLANE_DX_UM}) must be equal. Mixing them does not give a finer plane "
        f"on a coarse atlas -- it rescales the silhouette fit and produces a "
        f"registration that reports 100% coverage while being wrong.")

def outdir(name):
    """Return (creating if needed) an output directory under the study folder."""
    d = os.path.join(STUDY_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d
