"""Which atlas axis a section is cut along.

The BrainGlobe Allen volume is a single array with orientation ``asr`` and axes
``(AP, SI, LR)``. A coronal section is a plane at fixed AP; a sagittal section is
a plane at fixed LR. Same volume -- sagittal needs no extra download -- so the
only thing that changes is which axis is held constant and which two are drawn.

Everything downstream reads the plane through this module rather than assuming
axis 0, so adding a plane is a table entry rather than a hunt through
``registration.py``.

Axis roles, and why they are what they are:

    slice   the axis held constant; the value the user picks in step 3
    row     the image rows of the rasterised MERFISH tissue
    col     the image columns

``row`` is SI for both planes, because in both a section is imaged with the
dorsal-ventral axis in the image plane. ``col`` is LR for coronal and AP for
sagittal. Whether the tissue was actually mounted that way around is not this
module's problem -- that is exactly what the step-2 orientation (angle + flip)
exists to fix, including a 90 degree rotation.
"""

CORONAL = "coronal"
SAGITTAL = "sagittal"
DEFAULT = CORONAL

# name -> (slice_axis, row_axis, col_axis) into the (AP, SI, LR) volume
_AXES = {
    CORONAL:  (0, 1, 2),   # fixed AP; rows SI, cols LR
    SAGITTAL: (2, 1, 0),   # fixed LR; rows SI, cols AP
}

# What the step-3 slider is choosing, in words, per plane.
LABELS = {
    CORONAL:  {"axis": "AP", "long": "anterior-posterior",
               "hint": "Coronal: the atlas is sliced front to back."},
    SAGITTAL: {"axis": "ML", "long": "medial-lateral",
               "hint": "Sagittal: the atlas is sliced left to right."},
}

PLANES = (CORONAL, SAGITTAL)


def normalize(plane):
    """Accept None/empty as the default; reject anything unknown.

    Defaulting an unrecognised value to coronal would silently register a
    sagittal section against the wrong axis and label every cell from it, so an
    unknown plane is an error rather than a fallback.
    """
    if plane in (None, ""):
        return DEFAULT
    p = str(plane).strip().lower()
    if p not in _AXES:
        raise ValueError(f"unknown section plane {plane!r}; expected one of "
                         f"{', '.join(PLANES)}")
    return p


def axes(plane=DEFAULT):
    """(slice_axis, row_axis, col_axis) for this plane."""
    return _AXES[normalize(plane)]


def slice_axis(plane=DEFAULT):
    return axes(plane)[0]


def n_slices(shape, plane=DEFAULT):
    """How many slices the volume holds along this plane's slice axis."""
    return int(shape[slice_axis(plane)])


# A coronal HEMISECTION: tissue that is one half of the brain, split at the
# midline. Declared by the user in step 2, because the automatic fit cannot tell
# half a brain from a whole small one. "left"/"right" is the half of the slice
# AS DISPLAYED in steps 2 and 3 (dorsal up). The Allen annotation is bilaterally
# symmetric, so which animal hemisphere it was changes no label -- only which
# side of the tissue the midline is on.
HEMISECTIONS = ("left", "right")


def normalize_hemisection(value, plane=DEFAULT):
    """None for a whole section; "left"/"right" for a coronal hemisection.

    Sagittal sections are always one hemisphere, so a hemisection there means
    nothing and is refused rather than silently ignored."""
    if value in (None, "", "whole", "none"):
        return None
    v = str(value).strip().lower()
    if v not in HEMISECTIONS:
        raise ValueError(f"unknown hemisection {value!r}; expected left, right or whole")
    if normalize(plane) != CORONAL:
        raise ValueError("a hemisection applies to coronal sections only; a sagittal "
                         "section is already one hemisphere")
    return v


def hemisection_fit(atlas_mask, tissue_mask, col_coords, side):
    """Silhouette fit for a tissue that is ONE HALF of the atlas slice.

    The whole-section fit matches the tissue's span to the whole atlas slice and
    puts its centre on the midline, which stretches half a brain across both
    hemispheres (3-21% region agreement on the positive-control hemisections,
    2026-10-20). Here the atlas silhouette is cut at the midline (column
    coordinate 0; ``col_coords`` are the centred atlas coordinates of the
    slice's columns) and only the declared half is used, both for the scale
    and as the place the tissue's centre goes.

    Returns (row_scale, col_scale, col_centre_um): the scales, as
    atlas-span / tissue-span in pixels exactly like the whole-section fit, and
    the atlas column coordinate (microns) to put the tissue centre on.
    """
    import numpy as np
    col_coords = np.asarray(col_coords, float)
    keep = col_coords < 0 if side == "left" else col_coords > 0
    half = np.asarray(atlas_mask, bool) & keep[None, :]
    if not half.any():
        raise ValueError(f"the atlas slice has no tissue in its {side} half")

    def span(mask, axis):
        idx = np.where(mask.any(axis=1 - axis))[0]
        return (idx[-1] - idx[0] + 1) if idx.size else mask.shape[axis], idx

    tissue = np.asarray(tissue_mask, bool)
    (a_rows, _), (a_cols, cols) = span(half, 0), span(half, 1)
    (t_rows, _), (t_cols, _) = span(tissue, 0), span(tissue, 1)
    row_scale = float(np.clip(a_rows / max(t_rows, 1), 0.6, 1.6))
    col_scale = float(np.clip(a_cols / max(t_cols, 1), 0.6, 1.6))
    centre = float((col_coords[cols[0]] + col_coords[cols[-1]]) / 2.0)
    return row_scale, col_scale, centre


def axis_coords(shape, res):
    """Per-axis micron coordinates of an atlas volume (``xI`` in registration.py).

    Each axis is centred on zero, so the two resolutions of one atlas are offset
    by half a voxel: slice 150 at 50 um sits at 925.0 um, which falls exactly
    between slices 300 and 301 at 25 um. Carry a choice across resolutions by
    its micron value and ``nearest_index``, never by scaling the index.
    """
    import numpy as np
    return [np.arange(n) * res - (n - 1) * res / 2 for n in shape]


# The step-3 slider window, as fractions of the slice axis. These are the
# historical coronal bounds 40..230 of 264, so at 50 um they reproduce exactly
# what the slider always offered; as fractions they mean the same stretch of
# brain at any resolution. The first and last slices are mostly empty.
SLIDER_WINDOW = (0.15, 0.87)

# Where the slider starts for a job with no choice yet, in microns on the slice
# axis. Both are what the old hardcoded index 150 meant at 50 um on each plane,
# so the 50 um behaviour is unchanged; in microns they survive a resolution change.
DEFAULT_SLICE_UM = {CORONAL: 925.0, SAGITTAL: 1825.0}


def _js_round(x):
    """Math.round, so the backend and any client-side check agree on .5 cases."""
    import math
    return int(math.floor(x + 0.5))


def slider(shape, res, plane=DEFAULT):
    """Everything the step-3 slider needs for this plane at this resolution.

    ``lo``/``hi`` bound the slider, ``default_idx`` is where it starts, and
    ``origin_um``/``step_um`` let a client show the micron value of any index
    without re-deriving the centring convention.
    """
    plane = normalize(plane)
    n = n_slices(shape, plane)
    coords = axis_coords(shape, res)
    lo = _js_round(n * SLIDER_WINDOW[0])
    hi = _js_round(n * SLIDER_WINDOW[1])
    default = nearest_index(coords, DEFAULT_SLICE_UM[plane], plane)
    return {"n_slices": n, "lo": lo, "hi": hi,
            "default_idx": min(hi, max(lo, default)),
            "origin_um": float(coords[slice_axis(plane)][0]),
            "step_um": float(res)}


# The two ends of each volume axis, low index first. BrainGlobe's Allen volume is
# `asr`: the origin is anterior, superior and right, so index 0 on each axis is
# that end.
AXIS_ENDS = (("AP", "anterior", "posterior"),
             ("SI", "dorsal", "ventral"),
             ("LR", "right", "left"))


def beyond_atlas(points, shape, res):
    """Per point: is it past the end of the atlas volume, and off which end?

    ``points`` is (n, 3) atlas microns in the centred convention of
    ``axis_coords``. NaN coordinates (a cell with no sample point) are never
    beyond. Returns ``(any_end, {(axis, end_name): mask})``.

    The rule is the one the label lookup applies (``raster.sample_nearest``): a
    coordinate is inside iff its nearest voxel index is within ``0..n-1``.
    Pinned against that lookup in tests/test_atlas_overhang.py.
    """
    import numpy as np
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    coords = axis_coords(shape, res)
    any_end = np.zeros(len(pts), bool)
    ends = {}
    with np.errstate(invalid="ignore"):
        for a, (axis, lo_name, hi_name) in enumerate(AXIS_ENDS):
            idx = np.floor((pts[:, a] - coords[a][0]) / res + 0.5)
            for name, hit in ((lo_name, idx < 0), (hi_name, idx > shape[a] - 1)):
                ends[(axis, name)] = hit
                any_end |= hit
    return any_end, ends


def atlas_overhang(tf, row, col, inb, shape, res):
    """How many cells sample the atlas BEYOND its volume, and off which end.

    ``interp3D`` zero-fills there rather than clamping, so those cells are
    honestly unlabelled -- but unlabelled looks the same as background, and on a
    sagittal section it is not background: the Allen volume is flush at both AP
    ends, and AP is an in-plane axis, so the plane runs off the reference. This
    counts them so the UI can say so.

    ``tf`` is the per-pixel atlas coordinate in the same centred microns as
    ``axis_coords``; ``row``/``col``/``inb`` place each cell on it. The
    denominator is ALL cells, the same as coverage, so the two read together. A
    cell can overhang two axes and is then counted once in ``n`` and once under
    each end. The per-point rule is ``beyond_atlas``.
    """
    import numpy as np
    tf = np.asarray(tf)
    row = np.asarray(row); col = np.asarray(col)
    ib = (np.asarray(inb, bool) & (row >= 0) & (row < tf.shape[0])
          & (col >= 0) & (col < tf.shape[1]))
    n_cells = int(len(row))
    beyond, by_end = beyond_atlas(tf[row[ib], col[ib]], shape, res)
    ends = [{"axis": axis, "end": name, "n": int(m.sum()),
             "frac": int(m.sum()) / n_cells if n_cells else 0.0}
            for (axis, name), m in by_end.items() if m.any()]
    n = int(beyond.sum())
    return {"n": n, "frac": n / n_cells if n_cells else 0.0,
            "n_cells": n_cells, "ends": ends}


def slice_coordinate(axis_coords, index, plane=DEFAULT):
    """The micron coordinate of slice ``index``.

    ``axis_coords`` is the three-element list of per-axis coordinate vectors
    (``xI`` in registration.py), so this stays correct whatever the atlas
    resolution is.
    """
    return float(axis_coords[slice_axis(plane)][int(index)])


def nearest_index(axis_coords, micron, plane=DEFAULT):
    """The slice index closest to ``micron`` on this plane's axis.

    This is the resolution-independent way to carry a slice choice: the index
    changes when the atlas resolution changes, the micron value does not.
    """
    import numpy as np
    return int(np.argmin(np.abs(np.asarray(axis_coords[slice_axis(plane)]) - micron)))


def plane_points(row_coords, col_coords, plane=DEFAULT):
    """Atlas-space points for one image plane, shaped (1, nrow, ncol, 3).

    The slice axis is filled with zeros; the affine's translation on that axis is
    what moves the plane to the chosen slice. Row and column coordinates are
    placed on whichever atlas axes this plane assigns them to, which is the whole
    reason a caller does not need to know the plane.
    """
    import numpy as np
    s, r, c = axes(plane)
    R, C = np.meshgrid(np.asarray(row_coords, float),
                       np.asarray(col_coords, float), indexing="ij")
    pts = np.empty((1,) + R.shape + (3,), float)
    pts[0, ..., s] = 0.0
    pts[0, ..., r] = R
    pts[0, ..., c] = C
    return pts


def atlas_slice(volume, index, plane=DEFAULT):
    """One 2-D slice, with its axes already ordered (row, col) for this plane.

    ``np.take`` alone is not enough: for sagittal it leaves (AP, SI), which is
    (col, row), and silhouette spans measured on that would swap the two scale
    factors.
    """
    import numpy as np
    s, r, c = axes(plane)
    flat = np.take(np.asarray(volume), int(index), axis=s)
    # the two surviving axes, in their original order
    kept = [a for a in (0, 1, 2) if a != s]
    return flat if kept.index(r) == 0 else flat.T
