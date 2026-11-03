#!/usr/bin/env python
"""
Region-level Expression QC + geometry export for the MERFISH -> Allen pipeline.

For a REGISTERED region this produces the downstream / validation deliverables
agreed in review (2026-07-23):

  region_summary_<TAG>.csv          per Allen region: cell_count, percent, id/name
  region_gene_counts_<TAG>.csv      region x gene RAW counts (transcripts preferred,
                                    cell_by_gene fallback)
  region_expression_qc_<TAG>.csv    per-region correlation vs the Allen ABC reference
                                    (pearson / spearman / specificity) + PASS/WARN
  mapped_region_polygons_<TAG>.geojson   region polygons in the ORIGINAL MERFISH
                                    micron frame (for an independent viewer check)

Design notes
------------
* Correlation only SUPPORTS the registration -- it is not proof. Always review it
  together with the spatial overlay.
* Polygons are returned in the SAME coordinate frame as the input cell_metadata
  (center_x/center_y). The oriented density grid -> original micron mapping is
  recovered by a least-squares affine fit on the cell correspondence, so it is
  correct for any orientation path (angle/flip or TRANSPOSE) without re-deriving
  each path's math -- and is numerically verifiable (a region's polygon should
  enclose that region's cells).
* No shapely / geopandas dependency: GeoJSON is built from skimage contours + json.

Run:
  conda activate macat
  python m4_region_qc.py --region <region_key>            # writes the 4 files
  python m4_region_qc.py --region <region_key> --source cells  # force cell_by_gene
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

import config
from ..plane import closed_contours
from .m4_expr_corr import (resolve_mapped, load_allen_reference,
                           region_by_gene_transcripts, _sample_annotation_on_slice,
                           cross_correlation, diagonal_scores, MIN_CELLS)

ABC = os.path.join(config.REFERENCE_DATA_DIR, "allen_abc_region_by_gene.csv")
OUT = config.outdir("m4_output")

GATE = 0.30          # specificity self-corr PASS gate (see report 4.3 calibration)
MIN_GENES = 20       # correlation on fewer shared genes is unreliable
MIN_TX = 1000        # a region needs this many transcripts to score
MAX_POLY_PTS = 250   # subsample contour vertices to keep the GeoJSON small


# --------------------------------------------------------------------- summary
def region_summary(mapped_df):
    """Per Allen region cell counts from a mapped cell_metadata dataframe."""
    m = mapped_df[mapped_df["allen_region_id"] > 0]
    g = m.groupby("allen_acronym")
    summ = pd.DataFrame({
        "region_acronym": g.size().index,
        "region_id": g["allen_region_id"].first().values,
        "region_name": g["allen_name"].first().values,
        "cell_count": g.size().values,
    })
    summ["percent_cells"] = (summ["cell_count"] / max(len(m), 1) * 100).round(3)
    return summ.sort_values("cell_count", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------- gene counts
def region_gene_counts(region=None, source="auto", min_tx=MIN_TX, min_cells=MIN_CELLS,
                       *, mapped_csv=None, exp_path=None, fit_path=None,
                       angle=0, flip=None):
    """RAW region x gene counts. Returns (counts_df, totals, source_used).

    Config path: pass region. Web/upload path: pass mapped_csv + exp_path + fit_path
    + (angle, flip). source='transcripts' streams detected_transcripts
    (segmentation-free, preferred); 'cells' sums cell_by_gene per mapped region;
    'auto' picks transcripts when detected_transcripts is present, else cells.
    """
    exp_path = exp_path or (config.REGIONS[region] if region is not None else None)
    mapped_csv = mapped_csv or (resolve_mapped(region) if region is not None else None)
    has_tx = bool(exp_path) and any(
        os.path.exists(os.path.join(exp_path, f))
        for f in ("detected_transcripts.parquet", "detected_transcripts.csv"))
    if source == "auto":
        source = "transcripts" if has_tx else "cells"
    if source == "transcripts":
        counts, totals = region_by_gene_transcripts(
            region, min_tx=min_tx, normalize=False,
            fit_path=fit_path, exp_path=exp_path, angle=angle, flip=flip)
        return counts, totals.rename("total_transcripts"), "transcripts"
    # cell_by_gene fallback: SUM raw counts per mapped region
    mp = pd.read_csv(mapped_csv,
                     usecols=["EntityID", "allen_region_id", "allen_acronym"])
    cbg = pd.read_csv(os.path.join(exp_path, "cell_by_gene.csv"))
    cbg = cbg.rename(columns={cbg.columns[0]: "EntityID"})
    genes = [c for c in cbg.columns
             if c != "EntityID" and not c.lower().startswith("blank")]
    df = mp.merge(cbg[["EntityID"] + genes], on="EntityID", how="inner")
    df = df[df["allen_region_id"] > 0]
    n_cells = df.groupby("allen_acronym").size()
    counts = df.groupby("allen_acronym")[genes].sum()
    keep = n_cells[n_cells >= min_cells].index
    counts = counts.loc[keep]
    totals = counts.sum(axis=1).rename("total_transcripts")
    return counts, totals, "cells"


# ------------------------------------------------------------- correlation QC
def _match_genes(a, b):
    """Common genes between two column indexes, case-insensitive fallback."""
    common = [g for g in a if g in set(b)]
    if len(common) >= MIN_GENES:
        return common, common, common
    low = {g.lower(): g for g in b}
    ca, cb = [], []
    for g in a:
        if g.lower() in low:
            ca.append(g); cb.append(low[g.lower()])
    return ca, cb, ca


def expression_qc(counts, totals, cell_counts, abc, names=None):
    """Per-region correlation of the MERFISH profile vs the Allen ABC reference.

    counts : RAW region x gene DataFrame. Returns a QC DataFrame (one row/region).
    """
    from scipy.stats import pearsonr, spearmanr
    names = names or {}
    # fraction-normalised profile for the specificity (z-scored) metric
    frac = counts.div(counts.sum(axis=1).replace(0, np.nan), axis=0) * 1e4
    common_regions = [r for r in frac.index if r in abc.index]
    spec = {}
    if common_regions:
        C, _ = cross_correlation(frac.loc[common_regions], abc.loc[common_regions])
        for _, row in diagonal_scores(C).iterrows():
            spec[row["region"]] = row["self_corr"]

    rows = []
    for r in counts.index:
        tx = float(totals.get(r, np.nan))
        cc = int(cell_counts.get(r, 0))
        if r not in abc.index:
            rows.append(dict(region_acronym=r, region_name=names.get(r, r),
                             n_common_genes=0, pearson_r=np.nan, spearman_r=np.nan,
                             specificity_r=np.nan, cell_count=cc, total_transcripts=tx,
                             qc_status="WARN", notes="no matching ABC region"))
            continue
        gm, gr, _ = _match_genes(list(counts.columns), list(abc.columns))
        x = np.log1p(counts.loc[r, gm].to_numpy(float))
        y = np.log1p(abc.loc[r, gr].to_numpy(float))
        ok = np.isfinite(x) & np.isfinite(y)
        n = int(ok.sum())
        pr = float(pearsonr(x[ok], y[ok])[0]) if n >= 3 else np.nan
        sr = float(spearmanr(x[ok], y[ok])[0]) if n >= 3 else np.nan
        sp = float(spec.get(r, np.nan))
        notes = []
        if n < MIN_GENES:
            notes.append("few common genes")
        if np.isfinite(tx) and tx < MIN_TX:
            notes.append("few transcripts")
        gate = sp if np.isfinite(sp) else pr
        status = "PASS" if (np.isfinite(gate) and gate >= GATE and n >= MIN_GENES) else "WARN"
        rows.append(dict(region_acronym=r, region_name=names.get(r, r),
                         n_common_genes=n, pearson_r=pr, spearman_r=sr,
                         specificity_r=sp, cell_count=cc, total_transcripts=tx,
                         qc_status=status, notes="; ".join(notes)))
    qc = pd.DataFrame(rows)
    sort_key = qc["specificity_r"].fillna(qc["pearson_r"])
    return qc.assign(_k=sort_key).sort_values("_k", ascending=False).drop(columns="_k")\
             .reset_index(drop=True)


# ------------------------------------------------------------------ polygons
def _orient_fn_for(region):
    """The oriented-coordinate function used to BUILD the density grid for this
    region (config path). Web/uploaded regions pass their own via run_region_qc."""
    return lambda x, y: config.orient_xy(np.asarray(x), np.asarray(y), region)


def _signed_area(ring):
    """Shoelace area of a CLOSED ring [N,2] (last point == first). Sign encodes
    orientation: > 0 counter-clockwise in a standard x-y frame."""
    r = np.asarray(ring, float)
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1]))


def _is_simple(ring):
    """True when no two non-adjacent edges of the closed ring cross.

    Marching-squares output is always simple, but SIMPLIFYING it can introduce a
    self-intersection, which makes the exported polygon invalid. Vectorised per edge
    (n numpy passes, not n^2 python steps) so it is cheap enough to run on every
    ring of every region."""
    r = np.asarray(ring, float)[:-1]          # drop the duplicated closing vertex
    n = len(r)
    if n < 4:
        return True
    a = r
    b = np.roll(r, -1, axis=0)
    idx = np.arange(n)
    for i in range(n):
        m = (idx != i) & (idx != (i - 1) % n) & (idx != (i + 1) % n)
        if not m.any():
            continue
        aj, bj = a[m], b[m]
        di = b[i] - a[i]
        o1 = np.sign(di[0] * (aj[:, 1] - a[i][1]) - di[1] * (aj[:, 0] - a[i][0]))
        o2 = np.sign(di[0] * (bj[:, 1] - a[i][1]) - di[1] * (bj[:, 0] - a[i][0]))
        dj = bj - aj
        o3 = np.sign(dj[:, 0] * (a[i][1] - aj[:, 1]) - dj[:, 1] * (a[i][0] - aj[:, 0]))
        o4 = np.sign(dj[:, 0] * (b[i][1] - aj[:, 1]) - dj[:, 1] * (b[i][0] - aj[:, 0]))
        if np.any((o1 * o2 < 0) & (o3 * o4 < 0)):
            return False
    return True


def _close(ring):
    r = np.asarray(ring, float)
    if not np.array_equal(r[0], r[-1]):
        r = np.vstack([r, r[:1]])
    return r


def _nest_contours(contours):
    """Group marching-squares contours of ONE binary mask into
    [(shell_idx, [hole_idx, ...]), ...].

    Contours of a single mask never cross, so containment is decided by testing a
    single VERTEX of one ring against another (a ring's centroid is useless here --
    the centroid of an annulus's outer ring lies inside its hole). Nesting depth then
    alternates: even depth = shell, odd depth = hole, so an island inside a hole
    becomes its own shell. This is what the old code got wrong -- it appended EVERY
    contour as its own exterior ring, so holes were filled in solid.
    """
    from matplotlib.path import Path
    if not contours:
        return []
    paths = [Path(np.asarray(c, float)) for c in contours]
    depth = []
    for i, c in enumerate(contours):
        v = np.asarray(c, float)[0]
        depth.append(sum(1 for j, p in enumerate(paths) if j != i and p.contains_point(v)))
    out = []
    for i, d in enumerate(depth):
        if d % 2:
            continue                                  # a hole, attached below
        holes = []
        for k, dk in enumerate(depth):
            if dk != d + 1:
                continue
            # the unique depth-d ring containing this hole must be i
            if paths[i].contains_point(np.asarray(contours[k], float)[0]):
                holes.append(k)
        out.append((i, holes))
    return out


SIMPLIFY_AREA_TOL = 0.02     # a simplified ring may not change area by more than 2%


def _simplify_ring(ring, max_pts, px_um):
    """Reduce vertex count with Ramer-Douglas-Peucker, escalating the tolerance until
    the ring fits the vertex budget.

    A candidate is only accepted if it stays SIMPLE and preserves area to
    SIMPLIFY_AREA_TOL. Both guards matter: a coarse tolerance applied to a 1-2 px wide
    sliver (thin fiber tracts like `ccb`) cuts straight across it and can more than
    double the enclosed area. Correctness wins over the vertex budget -- if no
    candidate qualifies we return the full-resolution ring, so max_pts is a soft
    target, not a licence to emit a wrong polygon."""
    from pipeline_api import _rdp
    r = _close(ring)
    if len(r) <= max_pts:
        return r
    a0 = abs(_signed_area(r))
    eps = 0.25 * px_um
    for _ in range(6):
        s = _close(_rdp(r, eps))
        ok = (_is_simple(s) and
              (a0 <= 0 or abs(abs(_signed_area(s)) - a0) / a0 <= SIMPLIFY_AREA_TOL))
        if ok and len(s) <= max_pts:
            return s
        if not ok:
            break                 # coarser tolerances only distort further
        eps *= 2.0
    return r


def region_polygons(region=None, orient_fn=None, names=None, qc_by_region=None,
                    cell_counts=None, *, exp_path=None, fit_path=None,
                    angle=0, flip=None):
    """GeoJSON FeatureCollection of mapped Allen region polygons in the ORIGINAL
    MERFISH micron frame (same as cell_metadata center_x/center_y)."""
    from skimage import measure
    names = names or {}
    qc_by_region = qc_by_region or {}
    cell_counts = cell_counts or {}
    exp_path = exp_path or (config.REGIONS[region] if region is not None else None)
    if orient_fn is None:
        orient_fn = (_orient_fn_for(region) if region is not None else
                     (lambda x, y: config.orient_apply(np.asarray(x), np.asarray(y),
                                                       angle, flip)))

    AphiL, r0, c0, drow, dcol, transpose, acro = _sample_annotation_on_slice(
        region, fit_path=fit_path, exp_path=exp_path, angle=angle, flip=flip)

    meta = pd.read_csv(os.path.join(exp_path, "cell_metadata.csv"),
                       usecols=["center_x", "center_y"])
    cx = meta["center_x"].to_numpy(float); cy = meta["center_y"].to_numpy(float)
    ox, oy = orient_fn(cx, cy)                       # oriented coords (grid frame)
    # least-squares affine (oriented -> original): [ox,oy,1] @ M = [cx,cy]
    G = np.c_[ox, oy, np.ones_like(ox)]
    M, *_ = np.linalg.lstsq(G, np.c_[cx, cy], rcond=None)   # (3,2)

    def px_to_orig(rows, cols):
        if transpose:
            gx = r0 + rows * drow; gy = c0 + cols * dcol
        else:
            gy = r0 + rows * drow; gx = c0 + cols * dcol
        p = np.c_[gx, gy, np.ones_like(gx)] @ M
        return p[:, 0], p[:, 1]

    # pixel -> output-frame area factor: grid spacing, then the oriented->original fit
    px_area = abs(drow * dcol) * abs(float(np.linalg.det(np.asarray(M)[:2, :2])))
    px_um = float(np.sqrt(abs(drow * dcol)))
    feats = []
    area_check = {}
    ids = np.unique(AphiL); ids = ids[ids > 0]
    for rid in ids:
        ac = acro(rid)
        if ac is None:
            continue
        mask = AphiL == rid
        # padded, so a region on the raster edge is not closed by a chord
        contours = [c for c in closed_contours(mask) if len(c) >= 4]
        polys = []
        for shell_i, hole_ix in _nest_contours(contours):
            rings = []
            for k in [shell_i] + hole_ix:
                ct = _simplify_ring(contours[k], MAX_POLY_PTS, px_um)
                X, Y = px_to_orig(ct[:, 0], ct[:, 1])
                rings.append(_close(np.c_[X, Y]))
            # GeoJSON (RFC 7946): exterior counter-clockwise, holes clockwise. The
            # orientation must be fixed HERE, in the OUTPUT frame -- the
            # oriented->original affine can carry a reflection and flip every sign.
            for n, ring in enumerate(rings):
                want_ccw = (n == 0)
                if (_signed_area(ring) > 0) != want_ccw:
                    ring = ring[::-1]
                rings[n] = [[float(x), float(y)] for x, y in ring]
            polys.append(rings)                      # [exterior, hole, hole, ...]
        if not polys:
            continue
        # shell area MINUS hole area -- what the raster actually covers
        poly_area = sum(abs(_signed_area(p[0]))
                        - sum(abs(_signed_area(h)) for h in p[1:])
                        for p in polys)
        raster_area = int(mask.sum()) * px_area
        area_check[ac] = (poly_area, raster_area)
        feats.append({
            "type": "Feature",
            "properties": {
                "region_acronym": ac,
                "region_name": names.get(ac, ac),
                "region_id": int(rid),
                "cell_count": int(cell_counts.get(ac, 0)),
                "correlation": qc_by_region.get(ac),
                "area_um2": round(poly_area, 1),
                "n_parts": len(polys),
                "n_holes": sum(len(p) - 1 for p in polys),
            },
            "geometry": {"type": "MultiPolygon", "coordinates": polys},
        })
    # polygon-vs-raster area agreement, so callers (and the ROI cross-check) can
    # assert the export really covers the raster it was traced from
    region_polygons.last_area_check = area_check
    return {"type": "FeatureCollection", "features": feats}


# --------------------------------------------------------------------- driver
def run_region_qc(region=None, tag=None, outdir=OUT, source="auto",
                  abc_path=ABC, with_polygons=True, *, mapped_csv=None,
                  exp_path=None, fit_path=None, angle=0, flip=None):
    """Compute + WRITE the four region-level deliverables. Returns a dict of the
    four DataFrames/objects and their file paths.

    Config path: pass region (a config.REGIONS key). Web/upload path: pass
    mapped_csv + exp_path + fit_path + (angle, flip) as produced by a fresh
    pipeline_api.register_region run, plus a tag.
    """
    tag = tag or region
    os.makedirs(outdir, exist_ok=True)
    mapped_csv = mapped_csv or (resolve_mapped(region) if region is not None else None)
    exp_path = exp_path or (config.REGIONS[region] if region is not None else None)
    mapped = pd.read_csv(mapped_csv,
                         usecols=["allen_region_id", "allen_acronym", "allen_name"])
    summ = region_summary(mapped)
    names = dict(zip(summ["region_acronym"], summ["region_name"]))
    cell_counts = dict(zip(summ["region_acronym"], summ["cell_count"]))

    counts, totals, src = region_gene_counts(
        region, source=source, mapped_csv=mapped_csv, exp_path=exp_path,
        fit_path=fit_path, angle=angle, flip=flip)
    abc = load_allen_reference(abc_path)
    qc = expression_qc(counts, totals, cell_counts, abc, names=names)

    gene_tbl = counts.copy()
    gene_tbl.insert(0, "total_transcripts", totals.reindex(gene_tbl.index).values)
    gene_tbl.insert(0, "cell_count",
                    [cell_counts.get(r, 0) for r in gene_tbl.index])
    gene_tbl.insert(0, "region_name", [names.get(r, r) for r in gene_tbl.index])
    gene_tbl.index.name = "region_acronym"
    gene_tbl = gene_tbl.reset_index()

    paths = {
        "summary": os.path.join(outdir, f"region_summary_{tag}.csv"),
        "gene_counts": os.path.join(outdir, f"region_gene_counts_{tag}.csv"),
        "qc": os.path.join(outdir, f"region_expression_qc_{tag}.csv"),
    }
    summ.to_csv(paths["summary"], index=False)
    gene_tbl.to_csv(paths["gene_counts"], index=False)
    qc.to_csv(paths["qc"], index=False)

    geojson = None
    if with_polygons:
        qc_by_region = dict(zip(qc["region_acronym"],
                                qc["specificity_r"].where(qc["specificity_r"].notna(),
                                                          qc["pearson_r"])))
        geojson = region_polygons(region, orient_fn=None, names=names,
                                  qc_by_region=qc_by_region, cell_counts=cell_counts,
                                  exp_path=exp_path, fit_path=fit_path,
                                  angle=angle, flip=flip)
        paths["polygons"] = os.path.join(outdir, f"mapped_region_polygons_{tag}.geojson")
        with open(paths["polygons"], "w") as f:
            json.dump(geojson, f)

    n_pass = int((qc["qc_status"] == "PASS").sum())
    print(f"[region_qc {region}] source={src}  regions={len(qc)}  "
          f"PASS={n_pass}/{len(qc)}  "
          f"mean_pearson={qc['pearson_r'].mean():.3f}  "
          f"median_specificity={qc['specificity_r'].median():.3f}")
    print("  ->", "\n  -> ".join(paths.values()))
    return dict(summary=summ, gene_counts=gene_tbl, qc=qc, geojson=geojson,
                source=src, paths=paths)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--source", default="auto",
                    choices=["auto", "transcripts", "cells"])
    ap.add_argument("--no-polygons", action="store_true")
    a = ap.parse_args()
    run_region_qc(a.region, tag=a.tag, source=a.source,
                  with_polygons=not a.no_polygons)
