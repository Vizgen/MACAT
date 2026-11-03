"""Spatial hex-bin expression QC for the MERFISH -> Allen workflow.

This is an exploratory, AP-independent support metric.  Cells are grouped into
regular pointy-top hexagons in the original MERFISH micron frame.  Each valid
bin's expression profile is compared with every Allen ABC region profile.  The
reported ``self_corr`` is the correlation with the plurality mapped region;
``best_region`` is the highest-correlating reference region.

The result is deliberately not reduced to PASS/FAIL: thresholds have not yet
been calibrated against a positive control.  It is evidence for review, not
proof that a registration is correct.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd

import config


ABC = os.path.join(
    getattr(config, "REFERENCE_DATA_DIR", getattr(config, "STUDY_DIR", ".")),
    "allen_abc_region_by_gene.csv",
)
DEFAULT_HEX_SIZE_UM = 100.0
DEFAULT_MIN_CELLS = 10
MIN_COMMON_GENES = 20


def _cube_round(q, r):
    """Round fractional axial coordinates while preserving q+r+s == 0."""
    x = np.asarray(q, float)
    z = np.asarray(r, float)
    y = -x - z
    rx = np.rint(x); ry = np.rint(y); rz = np.rint(z)
    dx = np.abs(rx - x); dy = np.abs(ry - y); dz = np.abs(rz - z)
    mx = (dx > dy) & (dx > dz)
    my = (~mx) & (dy > dz)
    mz = ~(mx | my)
    rx[mx] = -ry[mx] - rz[mx]
    ry[my] = -rx[my] - rz[my]
    rz[mz] = -rx[mz] - ry[mz]
    return rx.astype(np.int64), rz.astype(np.int64)


def assign_hex(x, y, size_um=DEFAULT_HEX_SIZE_UM):
    """Return integer axial (q, r) ids and centers for pointy-top hexagons."""
    size = float(size_um)
    if not np.isfinite(size) or size <= 0:
        raise ValueError("hex size must be positive")
    x = np.asarray(x, float); y = np.asarray(y, float)
    qf = (math.sqrt(3.0) / 3.0 * x - y / 3.0) / size
    rf = (2.0 / 3.0 * y) / size
    q, r = _cube_round(qf, rf)
    cx = size * math.sqrt(3.0) * (q + r / 2.0)
    cy = size * 1.5 * r
    return q, r, cx, cy


def hex_vertices(cx, cy, size_um):
    """Closed six-sided ring for one pointy-top hexagon."""
    angles = np.deg2rad(np.arange(30.0, 390.0, 60.0))
    ring = np.c_[cx + size_um * np.cos(angles),
                 cy + size_um * np.sin(angles)]
    return np.vstack([ring, ring[:1]])


def _common_gene_columns(cbg_columns, abc_columns):
    abc_low = {str(g).lower(): str(g) for g in abc_columns}
    pairs = []
    for g in cbg_columns:
        gs = str(g)
        if gs == "EntityID" or gs.lower().startswith("blank"):
            continue
        ref = abc_low.get(gs.lower())
        if ref is not None:
            pairs.append((gs, ref))
    return pairs


def _row_unit(x):
    """Row-center and L2-normalize for Pearson correlation across genes."""
    x = np.asarray(x, float)
    x = x - np.nanmean(x, axis=1, keepdims=True)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-12)


def compute_hex_qc(mapped, cbg, abc, size_um=DEFAULT_HEX_SIZE_UM,
                   min_cells=DEFAULT_MIN_CELLS):
    """Compute one QC row per populated spatial hexagon.

    ``mapped`` must contain EntityID, center_x, center_y, allen_region_id and
    allen_acronym. ``cbg`` is the per-cell count table and ``abc`` the Allen
    region-by-gene reference.  Only bins with at least ``min_cells`` joined cells
    and ``MIN_COMMON_GENES`` shared genes are scored.
    """
    required = {"EntityID", "center_x", "center_y", "allen_region_id",
                "allen_acronym"}
    missing = required.difference(mapped.columns)
    if missing:
        raise ValueError("mapped table missing columns: " + ", ".join(sorted(missing)))
    if "EntityID" not in cbg.columns:
        cbg = cbg.rename(columns={cbg.columns[0]: "EntityID"})
    pairs = _common_gene_columns(cbg.columns, abc.columns)
    if len(pairs) < MIN_COMMON_GENES:
        raise ValueError(f"only {len(pairs)} genes shared with Allen reference; "
                         f"need at least {MIN_COMMON_GENES}")
    cbg_genes = [p[0] for p in pairs]
    abc_genes = [p[1] for p in pairs]
    df = mapped[list(required)].merge(cbg[["EntityID"] + cbg_genes],
                                      on="EntityID", how="inner")
    if df.empty:
        raise ValueError("no EntityID overlap between mapped and cell-by-gene tables")
    q, r, cx, cy = assign_hex(df["center_x"], df["center_y"], size_um)
    df = df.copy()
    df["hex_q"] = q; df["hex_r"] = r
    df["hex_center_x_um"] = cx; df["hex_center_y_um"] = cy

    keys = ["hex_q", "hex_r"]
    groups = df.groupby(keys, sort=True)
    n_cells = groups.size().rename("cell_count")
    valid = n_cells[n_cells >= int(min_cells)].index
    if len(valid) == 0:
        return pd.DataFrame(columns=[
            "hex_q", "hex_r", "center_x_um", "center_y_um", "cell_count",
            "labeled_cell_count", "mapped_region", "mapped_fraction",
            "n_expressed_genes", "self_corr", "best_region", "best_corr",
            "best_is_mapped"])

    counts = groups[cbg_genes].sum().loc[valid]
    centers = groups[["hex_center_x_um", "hex_center_y_um"]].first().loc[valid]
    labeled = df[df["allen_region_id"] > 0].groupby(keys).size()

    # Plurality mapped label per bin; unmapped cells do not vote.
    votes = (df[df["allen_region_id"] > 0]
             .groupby(keys + ["allen_acronym"]).size().rename("n").reset_index())
    if len(votes):
        votes = votes.sort_values(keys + ["n", "allen_acronym"],
                                  ascending=[True, True, False, True])
        winner = votes.drop_duplicates(keys).set_index(keys)
    else:
        winner = pd.DataFrame(columns=["allen_acronym", "n"])

    # Compare regional specificity using the Allen reference's own per-gene
    # baseline. Profiles are library-size normalized before log1p.
    A = abc[abc_genes].astype(float).copy()
    A.columns = cbg_genes
    A = A.div(A.sum(axis=1).replace(0, np.nan), axis=0) * 1e4
    Alog = np.log1p(A.to_numpy(float))
    mu = np.nanmean(Alog, axis=0, keepdims=True)
    sd = np.nanstd(Alog, axis=0, keepdims=True)
    sd[(sd == 0) | ~np.isfinite(sd)] = 1.0
    Az = _row_unit((Alog - mu) / sd)

    B = counts.to_numpy(float)
    expressed = (B > 0).sum(axis=1)
    B = B / np.maximum(B.sum(axis=1, keepdims=True), 1.0) * 1e4
    Bz = _row_unit((np.log1p(B) - mu) / sd)
    corr = np.clip(Bz @ Az.T, -1.0, 1.0)
    best_ix = np.argmax(corr, axis=1)
    ref_regions = np.asarray(A.index.astype(str))
    ref_pos = {r: i for i, r in enumerate(ref_regions)}

    rows = []
    for i, key in enumerate(valid):
        key = tuple(key) if isinstance(key, tuple) else (key,)
        nc = int(n_cells.loc[key])
        nl = int(labeled.get(key, 0))
        mapped_region = None
        mapped_n = 0
        if len(winner) and key in winner.index:
            w = winner.loc[key]
            mapped_region = str(w["allen_acronym"])
            mapped_n = int(w["n"])
        pos = ref_pos.get(mapped_region)
        self_corr = float(corr[i, pos]) if pos is not None else np.nan
        best_region = str(ref_regions[best_ix[i]])
        rows.append({
            "hex_q": int(key[0]), "hex_r": int(key[1]),
            "center_x_um": float(centers.loc[key, "hex_center_x_um"]),
            "center_y_um": float(centers.loc[key, "hex_center_y_um"]),
            "cell_count": nc, "labeled_cell_count": nl,
            "mapped_region": mapped_region,
            "mapped_fraction": mapped_n / max(nc, 1),
            "n_expressed_genes": int(expressed[i]),
            "self_corr": self_corr,
            "best_region": best_region,
            "best_corr": float(corr[i, best_ix[i]]),
            "best_is_mapped": bool(mapped_region == best_region),
        })
    return pd.DataFrame(rows)


def _render_hex_map(table, size_um, out_png, angle=None, flip=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    # Bins are assigned in the RAW MERFISH frame so the lattice (and therefore
    # every score in the table) is independent of the orientation pick. Only the
    # drawing is rotated, by pushing all polygon vertices through the SAME rigid
    # transform the registration uses, so the map reads in the dorsal-up frame of
    # the Results figures without changing a single number.
    # An orientation is applied when EITHER a rotation or a mirror was picked.
    # Testing `angle` alone silently dropped the whole transform for a pure
    # mirror (angle 0, flip "x") -- a perfectly ordinary step-2 pick, and the one
    # case where the raw frame and the dorsal-up frame differ by exactly the
    # thing the reviewer is trying to check.
    oriented = bool(angle) or flip is not None

    # Shift to a 0-based origin computed ONCE over every bin, the way
    # config.orient_apply shifts the pipeline's own coordinates. It must be
    # global: deriving it separately per collection would slide the scored and
    # unscored hexagons out of register with each other.
    if oriented and len(table):
        _all = np.concatenate([hex_vertices(x, y, size_um)[:-1] for x, y in
                               zip(table["center_x_um"], table["center_y_um"])])
        _ax, _ay = config.orient_rotflip(_all[:, 0], _all[:, 1], angle, flip)
        origin = (_ax.min(), _ay.min())
    else:
        origin = (0.0, 0.0)

    def _oriented(polys):
        if not polys or not oriented:
            return polys
        flat = np.concatenate(polys)
        rx, ry = config.orient_rotflip(flat[:, 0], flat[:, 1], angle, flip)
        rx = rx - origin[0]; ry = ry - origin[1]
        out, i = [], 0
        for poly in polys:
            n = len(poly)
            out.append(np.c_[rx[i:i + n], ry[i:i + n]])
            i += n
        return out

    valid = table[table["self_corr"].notna()]
    fig, ax = plt.subplots(figsize=(10, 7), facecolor="#0b0f14")
    ax.set_facecolor("#05070a")
    unscored = table[table["self_corr"].isna()]
    if len(unscored):
        missing_polys = _oriented([hex_vertices(x, y, size_um)[:-1]
                                   for x, y in zip(unscored["center_x_um"],
                                                   unscored["center_y_um"])])
        missing = PolyCollection(
            missing_polys, facecolors=(0.35, 0.38, 0.42, 0.45),
            edgecolors=(1, 1, 1, 0.16), linewidths=0.35,
        )
        ax.add_collection(missing)
    if len(valid):
        polys = _oriented([hex_vertices(x, y, size_um)[:-1]
                           for x, y in zip(valid["center_x_um"],
                                           valid["center_y_um"])])
        coll = PolyCollection(polys, array=valid["self_corr"].to_numpy(float),
                              cmap="inferno", clim=(-0.2, 0.8),
                              edgecolors=(1, 1, 1, 0.20), linewidths=0.35)
        ax.add_collection(coll)
        cb = fig.colorbar(coll, ax=ax, fraction=0.035, pad=0.02)
        cb.set_label("correlation with mapped Allen region", color="white")
        cb.ax.tick_params(colors="white")
    ax.autoscale_view()
    ax.set_aspect("equal"); ax.invert_yaxis()
    # y stays inverted in both frames: the registration rasterizes rows<->Y, so
    # increasing y is DOWN on screen, which is what puts cortex at the top once
    # the dorsal-up rotation is applied.
    frame = "dorsal-up" if oriented else "MERFISH"
    ax.set_xlabel(f"{frame} x (um)", color="white")
    ax.set_ylabel(f"{frame} y (um)", color="white")
    ax.set_title("Hex-bin expression support for mapped Allen regions",
                 color="white", pad=12)
    ax.tick_params(colors="#aeb8c4")
    for spine in ax.spines.values(): spine.set_color("#34404d")
    fig.tight_layout(); fig.savefig(out_png, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def run_hex_qc(mapped_csv, cbg_path, outdir, tag, abc_path=ABC,
               size_um=DEFAULT_HEX_SIZE_UM, min_cells=DEFAULT_MIN_CELLS,
               *, metadata_csv=None, angle=None, flip=None):
    """Compute, render and write the spatial hex-bin QC deliverables.

    ``angle``/``flip`` are the region's orientation pick. They affect the
    rendered PNG only -- binning and scoring always run in the raw MERFISH
    frame -- so the map can be read side by side with the Results figures.
    """
    # Keep the numerical core importable in lightweight test environments;
    # the existing reference loader also imports plotting dependencies.
    from .m4_expr_corr import load_allen_reference

    os.makedirs(outdir, exist_ok=True)
    wanted = ["EntityID", "center_x", "center_y", "allen_region_id",
              "allen_acronym"]
    header = pd.read_csv(mapped_csv, nrows=0).columns
    have_coords = {"center_x", "center_y"}.issubset(header)
    mapped_cols = [c for c in wanted if c in header]
    mapped = pd.read_csv(mapped_csv, usecols=mapped_cols)
    if not have_coords:
        if not metadata_csv or not os.path.exists(metadata_csv):
            raise ValueError("mapped CSV has no center_x/center_y and no cell metadata fallback")
        coords = pd.read_csv(metadata_csv, usecols=["EntityID", "center_x", "center_y"])
        mapped = mapped.merge(coords, on="EntityID", how="left")
    abc = load_allen_reference(abc_path)
    cbg_header = pd.read_csv(cbg_path, nrows=0).columns
    idcol = cbg_header[0]
    pairs = _common_gene_columns(
        ["EntityID" if c == idcol else c for c in cbg_header], abc.columns)
    use = [idcol] + [p[0] for p in pairs]
    cbg = pd.read_csv(cbg_path, usecols=use).rename(columns={idcol: "EntityID"})
    table = compute_hex_qc(mapped, cbg, abc, size_um=size_um,
                           min_cells=min_cells)
    stem = f"hex_expression_qc_{tag}_{int(round(size_um))}um"
    csv_path = os.path.join(outdir, stem + ".csv")
    png_path = os.path.join(outdir, stem + ".png")
    table.to_csv(csv_path, index=False)
    _render_hex_map(table, float(size_um), png_path, angle=angle, flip=flip)
    valid = table[table["self_corr"].notna()]
    summary = {
        "n_bins": int(len(table)), "n_scored": int(len(valid)),
        "n_cells": int(table["cell_count"].sum()) if len(table) else 0,
        "n_genes": int(len(pairs)),
        "median_self_corr": (float(valid["self_corr"].median())
                             if len(valid) else None),
        "best_match_agreement": (float(valid["best_is_mapped"].mean())
                                 if len(valid) else None),
        "hex_size_um": float(size_um), "min_cells": int(min_cells),
        "render_angle": (float(angle) if angle else 0.0),
        "render_flip": flip,
        # what the PNG is actually drawn in, so a stored JSON cannot disagree
        # with its own image about which frame it used
        "render_frame": ("dorsal-up" if (angle or flip is not None)
                         else "MERFISH"),
        "paths": {"csv": csv_path, "png": png_path},
    }
    with open(os.path.join(outdir, stem + ".json"), "w") as f:
        json.dump(summary, f, indent=2)
    return {"table": table, **summary}
