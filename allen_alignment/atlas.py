"""Allen atlas and ontology: loading the CCF volume, its provenance, the structure
tree, and rolling leaf annotations up to a hierarchy level. Owns every question
of the form "what IS this region" -- nothing here knows about registration,
deformation or figures."""
import numpy as np
import os
import config
from brainglobe_atlasapi import BrainGlobeAtlas
from importlib.metadata import PackageNotFoundError, version as package_version


RES = config.ATLAS_RES_UM

_ATLAS = None



def get_atlas():
    global _ATLAS
    if _ATLAS is None:
        _ATLAS = BrainGlobeAtlas(f"allen_mouse_{int(RES)}um", check_latest=False)
    return _ATLAS



class ResolutionMismatch(ValueError):
    """A bundle was registered on a different plane grid than this process runs.

    Its own subclass so the web layer can answer 409 ("this job needs
    re-registering") rather than 400 ("you sent something malformed").
    """


def bundle_res_um(npz):
    """The plane spacing a registration bundle was built on, in microns.

    Every bundle on disk on 2026-10-07 stores ``dx`` (all 59, all 50 um). One
    without it predates the 25 um atlas, so it is 50 um. Not inferred from the
    ``xJ0`` spacing: that matches ``dx`` on a real bundle but not on a synthetic
    test fixture, and a guess that is wrong there refuses a valid bundle.
    ``npz`` is an open ``np.load`` result or a dict.
    """
    keys = npz.files if hasattr(npz, "files") else npz.keys()
    return float(npz["dx"]) if "dx" in keys else LEGACY_BUNDLE_RES_UM


LEGACY_BUNDLE_RES_UM = 50.0


def check_bundle_resolution(npz, path=""):
    """Refuse a bundle whose plane grid is not the one this process runs on.

    ``AphiL``, ``row``/``col`` and the whole plane mesh are indexed on the grid
    the bundle was registered on. Reading a 50 um bundle under a 25 um config
    does not fail -- it mislabels, because every atlas lookup after that assumes
    the configured spacing. Re-registering is the only correct way forward, so
    say exactly that.
    """
    got = bundle_res_um(npz)
    want = float(config.PLANE_DX_UM)
    if abs(got - want) > 1e-6:
        raise ResolutionMismatch(
            f"this registration was made on the {got:g} um atlas and the server "
            f"now runs at {want:g} um. Re-pick the reference level and re-run "
            f"registration; earlier downloads are unaffected."
            + (f" bundle_path={path}" if path else ""))
    return got


def atlas_provenance(atlas=None):
    """Serializable identity of the exact reference dataset used by a run."""
    atlas = atlas or get_atlas()
    raw = getattr(atlas, "metadata", None) or {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        api_version = package_version("brainglobe-atlasapi")
    except PackageNotFoundError:
        api_version = None
    return {
        "atlas_id": f"allen_mouse_{int(RES)}um",
        "atlas_name": raw.get("name", "allen_mouse"),
        "dataset_version": str(raw.get("version", "1.2")),
        "resolution_um": list(raw.get("resolution", [float(RES)] * 3)),
        "citation": raw.get(
            "citation",
            "Wang et al. 2020, https://doi.org/10.1016/j.cell.2020.04.007",
        ),
        "atlas_link": raw.get("atlas_link", "http://www.brain-map.org"),
        "dataset_date": "2020-09-24",
        "brainglobe_atlasapi_version": api_version,
    }



def _coarsen_ids(bundle, depth, atlas=None):
    """Collapse leaf ids up to a given ontology depth (None = leaves)."""
    AphiL = bundle["AphiL"]
    if depth is None:
        return AphiL
    atlas = atlas or get_atlas(); struct = atlas.structures
    out = np.zeros_like(AphiL); cache = {}
    for u in np.unique(AphiL):
        if u <= 0:
            continue
        try:
            path = struct[int(u)]["structure_id_path"]
        except Exception:
            path = [int(u)]
        cache[u] = path[depth - 1] if len(path) >= depth else path[-1]
    for u, a in cache.items():
        out[AphiL == u] = a
    return out



# Curated alignment landmarks (Allen acronyms). Instead of the full ontology we
# collapse every leaf id to the named major structure it belongs to, so the
# editor shows ONE clean boundary per key anatomical landmark. These are the
# structures a human actually uses to judge AP / left-right / alignment.
LANDMARK_TIERS = {
    # sparse default: a handful of unambiguous structures
    "landmarks": ["Isocortex", "HPF", "TH", "VS"],
    # coarse anatomical divisions (Allen "major divisions" grey-matter blocks +
    # ventricles). Explicit parent-acronym ALLOWLIST -- every leaf id is folded
    # up to the FIRST of these it descends from, so branches with uneven ontology
    # depth still merge to a consistent coarse granularity (never a fixed depth).
    # Fiber tracts are excluded (they shatter into many tiny pieces). This is the
    # Manual-curation DEFAULT: enough internal structure to align by, but not so
    # fine that you must nudge individual sub-nuclei.
    "coarse": ["Isocortex", "OLF", "HPF", "CTXsp", "STR", "PAL",
               "TH", "HY", "MB", "P", "MY", "CB", "VS"],
    # major grey-matter brain divisions (fiber tracts excluded: they fragment
    # into dozens of tiny pieces and clutter the alignment view)
    "major": ["Isocortex", "OLF", "HPF", "CTXsp", "STR", "PAL",
              "TH", "HY", "MB", "CB", "VS"],
}


# Single source of truth for the boundary tier shown BOTH in the auto-registration
# preview and the manual-curation editor, so the two steps always draw the exact
# same blue lines (same generator, same tier, same features).
CURATION_TIER = "coarse"



# Single source of truth for how each COARSE Allen division is labelled + coloured
# in the UI. Shared by the curation in-canvas labels/legend, the registration
# figure and the results composition, so a region looks identical in every step.
# `short` is the compact acronym drawn inside the region; `color` is its swatch.
COARSE_REGION_STYLE = {
    "Isocortex": {"short": "CTX",   "color": "#79c17e"},
    "OLF":       {"short": "OLF",   "color": "#a7cf79"},
    "HPF":       {"short": "HPF",   "color": "#57c6a4"},
    "CTXsp":     {"short": "CTXsp", "color": "#7fd0c2"},
    "STR":       {"short": "STR",   "color": "#7f9cd6"},
    "PAL":       {"short": "PAL",   "color": "#6d7ec7"},
    "TH":        {"short": "TH",    "color": "#ff9bb0"},
    "HY":        {"short": "HY",    "color": "#e79ac2"},
    "MB":        {"short": "MB",    "color": "#ffb266"},
    "P":         {"short": "P",     "color": "#f0a15a"},
    "MY":        {"short": "MY",    "color": "#df8a4e"},
    "CB":        {"short": "CB",    "color": "#e6d264"},
    "VS":        {"short": "VS",    "color": "#9aa6b2"},
}

COARSE_REGION_DEFAULT_COLOR = "#8aa0b8"



def region_style(acro):
    """Canonical {short, color} for a coarse division acronym (never fails)."""
    return COARSE_REGION_STYLE.get(
        acro, {"short": acro, "color": COARSE_REGION_DEFAULT_COLOR})



def _group_by_landmarks(bundle, acronyms, atlas):
    """Map each leaf id in AphiL to the id of the FIRST landmark ancestor it
    descends from (0 = not part of any requested landmark)."""
    struct = atlas.structures
    want = []
    for a in acronyms:
        try:
            want.append((int(struct[a]["id"]), a))
        except Exception:
            pass
    AphiL = bundle["AphiL"]
    out = np.zeros_like(AphiL)
    label_acro = {}
    for u in np.unique(AphiL):
        if u <= 0:
            continue
        try:
            path = set(struct[int(u)]["structure_id_path"])
        except Exception:
            path = {int(u)}
        for gid, ga in want:
            if gid in path:
                out[AphiL == u] = gid
                label_acro[gid] = ga
                break
    return out, label_acro



# ======================================================= depth-2 read-only overlay
# Phase-1 manual curation shows the Allen structures rolled up to their LITERAL
# structure-tree depth 2 (the `structure_tree_safe_2017.csv` `depth` field, i.e.
# the index into `structure_id_path` with root at index 0). These boundaries are a
# READ-ONLY reference overlay drawn in each structure's official Allen colour; the
# user aligns them with the GLOBAL affine box, never by dragging the lines. The
# per-cell label raster stays LEAF-level -- only the overlay is coarsened.
STRUCTURE_TREE_CSV = os.path.join(config.REFERENCE_DATA_DIR,
                                  "structure_tree_safe_2017.csv")

DEPTH2_LEVEL = 2          # literal CSV depth for the read-only overlay

_ROOT_ID = 997

_stree_cache = None



def _load_structure_tree(path=STRUCTURE_TREE_CSV):
    """Cached loader of the Allen structure tree CSV -> {id: {path(list[int]),
    acronym, name, color('#rrggbb'), depth}}. Authoritative hierarchy source for
    the depth-2 overlay (structure_tree_safe_2017.csv)."""
    global _stree_cache
    if _stree_cache is not None:
        return _stree_cache
    import csv as _csv
    from allen_alignment.reference_files import require
    S = {}
    with open(require(path), newline="") as fh:
        for r in _csv.DictReader(fh):
            try:
                sid = int(r["id"])
            except (KeyError, ValueError):
                continue
            S[sid] = {
                "acronym": r.get("acronym", str(sid)),
                "name": r.get("name", ""),
                "depth": int(r["depth"]) if r.get("depth") else 0,
                "path": [int(x) for x in
                         r.get("structure_id_path", "").strip("/").split("/") if x],
                "color": "#" + (r.get("color_hex_triplet") or "999999").strip().lstrip("#"),
            }
    _stree_cache = S
    return S



def _ancestor_at_depth(path, d):
    """Ancestor id at literal CSV depth ``d`` (index == depth; root at index 0).
    Returns None when the structure is SHALLOWER than ``d`` (i.e. it has no
    depth-``d`` ancestor). We deliberately do NOT clamp to the leaf: a voxel
    annotated directly at a coarse level such as ``fiber tracts`` (depth 1) or
    ``ventricular systems`` (depth 1) genuinely has no depth-2 ancestor and must be
    reported as unmapped/background rather than kept at its own sub-depth-2 id."""
    return path[d] if d < len(path) else None



def _group_by_depth2(bundle, level=DEPTH2_LEVEL):
    """Fold every leaf id in AphiL up to its literal depth-``level`` ancestor via
    structure_id_path. Returns (idmap, meta) where meta[gid] = {acronym, name,
    color}. A leaf with no depth-``level`` ancestor (root, or a structure annotated
    directly at depth < level such as fiber tracts / ventricular systems) is mapped
    to 0 = unmapped/background and counted on ``_group_by_depth2.last_unmapped_px``;
    it is never drawn and never falls back to its own id."""
    S = _load_structure_tree()
    AphiL = np.asarray(bundle["AphiL"])
    out = np.zeros_like(AphiL)
    meta = {}
    unmapped_px = 0
    for u in np.unique(AphiL):
        iu = int(u)
        if iu <= 0:
            continue
        s = S.get(iu)
        gid = _ancestor_at_depth(s["path"], level) if s else None
        if not gid or gid == _ROOT_ID or gid <= 0:   # no depth-2 ancestor -> background
            unmapped_px += int((AphiL == u).sum())
            continue
        out[AphiL == u] = gid
        if gid not in meta:
            g = S.get(int(gid), {})
            meta[int(gid)] = {"acronym": g.get("acronym", str(gid)),
                              "name": g.get("name", ""),
                              "color": g.get("color", COARSE_REGION_DEFAULT_COLOR)}
    _group_by_depth2.last_unmapped_px = int(unmapped_px)
    return out, meta
