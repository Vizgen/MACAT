"""Anatomical marker-gene background.

A curation background that shows WHERE THE TISSUE ITSELF SAYS the major brain
divisions are, rendered on the same plane grid as the DAPI and cell-density
layers so it can be switched between them without the atlas overlay moving.

Each coarse Allen division has a short list of canonical markers
(data/reference/anatomical_markers.csv). Per cell the counts of a division's
markers are summed, binned onto the registration grid, and drawn in THAT
DIVISION'S OWN ALLEN COLOUR -- the same colour allen_alignment.atlas.region_style
gives the overlay. So a cortex marker blob and the cortex outline drawn on top of
it are the same green, and a misalignment is visible as a colour offset rather
than something the eye has to reason about.

This is a reference layer, not evidence: it is the measured expression, with no
atlas involved, so agreeing with the overlay is informative and disagreeing with
it means the registration, the markers, or the panel is at fault -- in that order
of likelihood.
"""
import os

import numpy as np
import pandas as pd

import config
from .atlas import bundle_res_um, region_style

MARKER_CSV = os.path.join(config.REFERENCE_DATA_DIR, "anatomical_markers.csv")

# A division needs at least this many of its markers present in the panel before
# it is drawn. Below it the "division" is really one or two genes and the layer
# would imply an anatomical claim the data cannot support.
MIN_GENES_PER_REGION = 3

# Percentile each division is scaled to. Per division, not global: panels differ
# by an order of magnitude in how deeply any one division is covered, and a
# global stretch would leave the shallow ones invisible.
NORM_PERCENTILE = 99.5

# Output grid, as a multiple of the 50 um registration plane grid.
#
# This is a canvas for the kernel below, NOT a claim of new information: with
# ~106k cells over ~50 mm2 the cells sit about 22 um apart, so a 16.7 um pixel is
# already finer than the data. Supersampling ALONE makes the layer worse --
# measured, at 3x with no kernel the per-pixel division disagreement rises from
# 50% to 66%, because most fine bins then hold a single cell and the "dominant
# division" is just whatever that one cell expressed.
PLANE_SUPERSAMPLE = 3

# Gaussian kernel applied to each division's binned counts, in MICRONS, so it
# means the same thing whatever the plane spacing is.
#
# The old layer was not blurry so much as noisy: at 50 um and ~5 cells/bin, 50%
# of visible pixels disagreed with a neighbour about which division won, and the
# canvas hid that by smoothing a 233 px image across ~1100 screen px. Doing the
# smoothing here, deliberately, instead of leaving it to the browser's bilinear
# stretch:
#
#     grid              sigma   speckle   saturation
#     233x137 (old)      --      50.2%      0.242
#     699x411             0      66.3%      0.298
#     699x411            25 um   13.0%      0.284
#     699x411            40 um    8.0%      0.284
#
# 40 um reads as anatomy -- hippocampal band, thalamus, cortex and fiber tracts
# separate cleanly. 25 um keeps visible cell-level texture and is the value to
# try if this ever looks over-smoothed; below about 20 um the speckle comes back.
KDE_SIGMA_UM = 40.0


def load_markers(path=MARKER_CSV):
    """Return {region_acronym: {"name": str, "genes": [str, ...]}} in file order."""
    df = pd.read_csv(path)
    need = {"region_acronym", "region_name", "gene"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing column(s): {sorted(missing)}")
    out = {}
    for acro, grp in df.groupby("region_acronym", sort=False):
        out[str(acro)] = {"name": str(grp["region_name"].iloc[0]),
                          "genes": [str(g) for g in grp["gene"]]}
    return out


def match_panel(columns, markers=None):
    """Which divisions this panel can actually support.

    ``columns`` are the cell_by_gene column names. Returns a list of dicts, one
    per division, ordered as the CSV is, each with the genes present, the genes
    absent, and whether the division clears MIN_GENES_PER_REGION. Gene symbols
    are matched case-insensitively: panels are not consistent about casing and a
    silent miss here would quietly drop a whole division from the picture.
    """
    markers = markers or load_markers()
    lower = {str(c).lower(): str(c) for c in columns}
    report = []
    for acro, spec in markers.items():
        present = [lower[g.lower()] for g in spec["genes"] if g.lower() in lower]
        absent = [g for g in spec["genes"] if g.lower() not in lower]
        style = region_style(acro)
        report.append({
            "acronym": acro, "name": spec["name"], "color": style["color"],
            "short": style["short"], "present": present, "absent": absent,
            "n_present": len(present), "n_total": len(spec["genes"]),
            "used": len(present) >= MIN_GENES_PER_REGION,
        })
    return report


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float) / 255.0


def region_planes(bundle, exp_path, markers=None, min_genes=MIN_GENES_PER_REGION,
                  only=None, supersample=PLANE_SUPERSAMPLE, sigma_um=KDE_SIGMA_UM):
    """Per-division marker density on the bundle's J-plane grid.

    Returns (planes, report) where ``planes`` maps acronym -> float array shaped
    like ``Jimg`` scaled by ``supersample``. Only divisions that clear
    ``min_genes`` are computed. ``supersample=1, sigma_um=0`` reproduces the
    original 50 um binning exactly.

    Cells are binned from the registration's OWN continuous coordinates
    (``xr``/``yr``, which are what ``row``/``col`` were rounded from) so the
    kernel sees where each cell actually is rather than which 50 um box it fell
    in. The fine grid covers the SAME footprint as the coarse one -- ``supersample``
    subdivisions per coarse pixel, with the coarse pixel centre kept as the centre
    of the middle sub-pixel -- so the layer still lands on exactly the extent the
    density and DAPI backgrounds are drawn over and switching between them cannot
    shift the image.

    (Honest note: at sigma 40 um the continuous coordinates make no measurable
    difference -- 8.0% speckle vs 7.9% from the rounded indices -- because the
    kernel is wider than the 50 um quantisation it undoes. They matter only if
    sigma is lowered, which is exactly when the quantisation would start to show.)

    cell_by_gene is joined on EntityID rather than
    assumed to share row order with cell_metadata -- it usually does, but an
    unchecked assumption here would silently smear every marker across the
    section.
    """
    cbg_path = os.path.join(exp_path, "cell_by_gene.csv")
    meta_path = os.path.join(exp_path, "cell_metadata.csv")
    if not os.path.exists(cbg_path):
        raise FileNotFoundError(f"no cell_by_gene.csv in {exp_path}")
    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"no cell_metadata.csv in {exp_path}")

    header = pd.read_csv(cbg_path, nrows=0).columns
    report = match_panel(header, markers)
    # `only` restricts which divisions are rendered (the legend toggles). The
    # report still describes the whole panel, so the legend can offer a division
    # that is currently switched off.
    wants = (lambda r: r["n_present"] >= min_genes
             and (only is None or r["acronym"] in only))
    wanted = sorted({g for r in report if wants(r) for g in r["present"]})
    if not wanted:
        return {}, report

    idcol = header[0]
    cbg = pd.read_csv(cbg_path, usecols=[idcol] + wanted)
    order = pd.read_csv(meta_path, usecols=["EntityID"])
    # align cell_by_gene to the bundle's cell order (== cell_metadata order)
    cbg = cbg.set_index(idcol).reindex(order["EntityID"].to_numpy())

    from scipy import ndimage

    shape = np.asarray(bundle["Jimg"]).shape
    inb = np.asarray(bundle["inb"]).astype(bool)
    ss = max(1, int(supersample))
    fine = (shape[0] * ss, shape[1] * ss)

    if "xr" in bundle and "yr" in bundle:
        # registration binned with row = rint((yr - r0) / drow); the fine index
        # that tiles the SAME footprint is therefore
        #     floor(((yr - r0) / drow + 0.5) * ss)
        # which collapses back to exactly `row` when ss == 1.
        r0, c0 = float(bundle["r0"]), float(bundle["c0"])
        drow, dcol = float(bundle["drow"]), float(bundle["dcol"])
        fr = np.floor(((np.asarray(bundle["yr"], float) - r0) / drow + 0.5) * ss)
        fc = np.floor(((np.asarray(bundle["xr"], float) - c0) / dcol + 0.5) * ss)
        fr = fr.astype(np.int64); fc = fc.astype(np.int64)
    else:
        # bundle predates xr/yr: fall back to the rounded indices, placed at the
        # centre of their sub-block. Costs sub-pixel placement, nothing else.
        fr = np.asarray(bundle["row"]).astype(np.int64) * ss + ss // 2
        fc = np.asarray(bundle["col"]).astype(np.int64) * ss + ss // 2
        drow = dcol = bundle_res_um(bundle)

    keep = (inb & (fr >= 0) & (fr < fine[0])
            & (fc >= 0) & (fc < fine[1]))
    flat = fr[keep] * fine[1] + fc[keep]

    # sigma in FINE pixels, per axis, so an anisotropic plane grid still gets an
    # isotropic kernel in microns
    sigma_px = (float(sigma_um) / (drow / ss), float(sigma_um) / (dcol / ss))

    planes = {}
    for r in report:
        if not wants(r):
            continue
        score = cbg[r["present"]].to_numpy(dtype=np.float64)
        score = np.nan_to_num(score).sum(axis=1)[keep]
        plane = np.bincount(
            flat, weights=score, minlength=fine[0] * fine[1]).reshape(fine)
        if sigma_um > 0:
            plane = ndimage.gaussian_filter(plane, sigma_px)
        planes[r["acronym"]] = plane
    return planes, report


# How sharply the dominant division wins a pixel. 1.0 is the plain mixture, which
# on a 14-division panel is muddy because no division is ever exactly zero.
#
# 5.0, not the 2.5 first shipped: at 2.5 the blend between fourteen divisions --
# two of which (VS, fiber tracts) are themselves grey -- desaturates the tissue
# into a pale wash that reads as a DAPI image showing through, even though no
# DAPI is drawn. Measured mean saturation over visible pixels: 0.184 at 2.5,
# 0.242 at 5.0, 0.292 at 9.0, with 9.0 starting to speckle. 5.0 separates cortex,
# hippocampus, thalamus and striatum cleanly while real borders still blend.
SHARPEN = 5.0


def composite_rgba(planes, report, percentile=NORM_PERCENTILE, sharpen=SHARPEN):
    """RGBA composite, one Allen colour per division.

    Two normalisations, and both are needed:

    1. PER DIVISION, to its own ``percentile``. Panels differ by an order of
       magnitude in how deeply any one division is covered, so without this the
       shallow divisions are invisible.
    2. PER PIXEL, across divisions, into a composition that sums to 1. The colour
       is then a weighted average of the Allen colours rather than a sum.

    Step 2 is what makes the layer readable. Summing 14 per-division-normalised
    channels additively -- the obvious reading of "additive composite" -- saturates
    to white almost everywhere, because every division is scaled to full brightness
    wherever it has any signal at all. Rendering it that way produced a picture
    with the anatomy visible in the texture and no colour information left.

    Brightness (and alpha) carries the TOTAL marker signal, so the layer is dark
    off-tissue, can sit under the atlas overlay, and never claims a division in a
    pixel that has no reads.
    """
    if not planes:
        return None
    acros = list(planes.keys())
    shape = planes[acros[0]].shape
    colors = {r["acronym"]: r["color"] for r in report}

    # (1) per-division normalisation
    W = np.zeros((len(acros),) + shape, float)
    for i, acro in enumerate(acros):
        shown = np.log1p(np.clip(planes[acro], 0, None))
        pos = shown[shown > 0]
        hi = float(np.percentile(pos, percentile)) if pos.size else 1.0
        W[i] = np.clip(shown / max(hi, 1e-12), 0.0, 1.0)

    # (2) per-pixel composition, sharpened so the dominant division reads clearly
    P = W ** float(sharpen)
    denom = P.sum(axis=0)
    good = denom > 1e-12
    P = np.where(good[None], P / np.where(good, denom, 1.0)[None], 0.0)

    rgb = np.zeros(shape + (3,), float)
    for i, acro in enumerate(acros):
        rgb += P[i][..., None] * _hex_to_rgb(colors.get(acro, "#8aa0b8"))

    intensity = np.clip(W.max(axis=0), 0.0, 1.0)      # how much signal is here
    out = np.zeros(shape + (4,), np.uint8)
    out[..., :3] = (np.clip(rgb * intensity[..., None], 0, 1) * 255).astype(np.uint8)
    out[..., 3] = (intensity * 255).astype(np.uint8)
    return out
