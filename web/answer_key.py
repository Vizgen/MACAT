"""Known answers that travel with the data: a region folder's ``expected.json``.

A region folder may hold an ``expected.json`` next to its cell tables, giving
the correct answers for that section. When it does, MACAT shows the known atlas
level in step 1's "Your sections", pre-fills step 2 (plane, half, rotation,
flip) and opens step 3 on that level. It is read from the data wherever it lives
-- a local folder, a copy elsewhere, or an S3 prefix -- so the answers never
depend on a list kept inside MACAT. The Allen annotated data ships one per
section (docs/allen_annotated_data.md); any dataset may supply one.

Fields MACAT reads (all optional; anything else in the file is ignored):

    plane                 "coronal" or "sagittal"
    hemisection           "left", "right", or "" for a whole section
    orientation_angle     degrees, as step 2's rotation
    orientation_flip      "none", "x" (vertical) or "y" (horizontal)
    idx_25um              atlas level index at 25 um ...
    idx_25um_tolerance    ... its +/- tolerance, in levels
    idx_25um_mirror       ... and, for sagittal, the mirror index across the midline
    idx_50um, idx_50um_tolerance, idx_50um_mirror   the same at 50 um

The level is read at the server's atlas resolution (idx_<res>um).
"""
import json
import os

FILE = "expected.json"


def read(folder):
    """The answer key in `folder`, as a dict; None if absent or unreadable."""
    if not folder:
        return None
    try:
        with open(os.path.join(folder, FILE)) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _int(v):
    try:
        return None if v in (None, "") else int(float(v))
    except (TypeError, ValueError):
        return None


def known_level(ans, res_um):
    """{idx, tol, mirror, res_um} at resolution `res_um`, or None."""
    if not ans:
        return None
    res = int(round(float(res_um)))
    idx = _int(ans.get(f"idx_{res}um"))
    if idx is None:
        return None
    return {"idx": idx, "tol": _int(ans.get(f"idx_{res}um_tolerance")) or 0,
            "mirror": _int(ans.get(f"idx_{res}um_mirror")), "res_um": res}


def orientation(ans):
    """What step 2 is pre-filled with: {plane, hemisection, angle, flip}, only
    for the fields the key gives; None if it gives none."""
    if not ans:
        return None
    from allen_alignment import planes as _planes
    out = {}
    if ans.get("plane"):
        try:
            out["plane"] = _planes.normalize(ans["plane"])
        except ValueError:
            pass
    plane = out.get("plane", "coronal")
    if ans.get("hemisection"):
        try:
            out["hemisection"] = _planes.normalize_hemisection(ans["hemisection"], plane)
        except ValueError:
            pass
    try:
        if ans.get("orientation_angle") not in (None, ""):
            out["angle"] = float(ans["orientation_angle"]) % 360.0
    except (TypeError, ValueError):
        pass
    flip = str(ans.get("orientation_flip") or "").strip().lower()
    if flip in ("none", "x", "y"):
        out["flip"] = flip
    return out or None


def summary(ans, res_um):
    """The small part step 1 shows beside a section: level and plane."""
    if not ans:
        return None
    o = orientation(ans) or {}
    return {"known_level": known_level(ans, res_um), "plane": o.get("plane"),
            "hemisection": o.get("hemisection")}
