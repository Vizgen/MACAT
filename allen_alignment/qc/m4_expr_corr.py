#!/usr/bin/env python
"""
M4 gene-expression-correlation QC (design + first implementation).

Idea (from the 2026-07-14 meeting): if a MERFISH region is mapped to Allen region
R, its gene-expression profile should correlate with Allen's reference profile for
R -- and correlate MORE with R than with any other region. We build a region x gene
profile from the MERFISH side, a matching one from an Allen reference, and compute
a region-by-region cross-correlation. A good mapping => strong diagonal
(each mapped region matches its own Allen reference), weak off-diagonal.

Two MERFISH input sources (meeting action item):
  A. cell-by-gene table   -- IMPLEMENTED here. Per-cell counts grouped by the
                             Allen label already written to the mapped CSV.
                             Depends on cell segmentation quality.
  B. detected_transcripts -- SPECIFIED, not yet implemented (see design md +
                             region_by_gene_transcripts). Segmentation-free:
                             assign each transcript to an Allen region by its
                             (global_x, global_y) using the SAME fitted warp as
                             m3 (m3_fit_3d<TAG>.pt), then count gene x region.

Allen reference side is an EXTERNAL dependency :
pass --allen <region_by_gene.csv>. Without it, the script runs a SELF-CONSISTENCY
check (split each region's cells in half and cross-correlate) so the metric
machinery + normalization can be validated today, before any Allen data arrives.

Run:
    conda activate macat
    REGION=<region_key> python m4_expr_corr.py              # self-consistency check
    REGION=<region_key> python m4_expr_corr.py --allen allen_ref.csv  # real QC
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config

M3 = os.path.join(config.STUDY_DIR, "m3_output")
MIN_CELLS = 50        # skip regions with too few cells (noisy profile)
TOP_REGIONS = 25      # cap regions shown in the heatmap for readability


# --------------------------------------------------------------------------- IO
def resolve_mapped(region):
    """Find the deliverable mapped CSV for a region (prefers a _manual variant)."""
    tag = "" if region == "sw234" else "_" + region
    cands = [f"cell_metadata_with_allen_regions{tag}_manual.csv",
             f"cell_metadata_with_allen_regions{tag}.csv",
             f"cell_metadata_with_allen_regions{tag}_deepslice_frozen.csv"]
    for c in cands:
        p = os.path.join(M3, c)
        if os.path.exists(p):
            return p
    hits = glob.glob(os.path.join(M3, f"cell_metadata_with_allen_regions*{region}*.csv"))
    if hits:
        return hits[0]
    raise FileNotFoundError(f"no mapped CSV for region {region} in {M3}")


def resolve_fit(region):
    """Find the m3 fit for a region (same preference order as resolve_mapped
    so the fit and its mapped CSV are the SAME run)."""
    tag = "" if region == "sw234" else "_" + region
    for c in (f"m3_fit_3d{tag}_manual.npz", f"m3_fit_3d{tag}.npz",
              f"m3_fit_3d{tag}_deepslice_frozen.npz"):
        p = os.path.join(M3, c)
        if os.path.exists(p):
            return p
    hits = glob.glob(os.path.join(M3, f"m3_fit_3d*{region}*.npz"))
    if hits:
        return hits[0]
    raise FileNotFoundError(f"no m3 fit for region {region} in {M3}")


def load_fit(path):
    """A registration fit as plain numpy: {A, xI, dx, atlas}.

    Fits are .npz since 2026-10-27 (they were torch .pt files before; existing ones
    were converted then, beside the originals). A job record may still
    name the .pt, so a .pt path is read from the .npz next to it. Never unpickles
    anything (allow_pickle=False)."""
    if path.endswith(".pt"):
        path = path[:-3] + ".npz"
    if not os.path.exists(path):
        raise FileNotFoundError(f"no registration fit at {path}")
    z = np.load(path, allow_pickle=False)
    return {"A": z["A"], "xI": [z[f"xI{i}"] for i in range(3)],
            "dx": float(z["dx"]), "atlas": str(z["atlas"])}


def region_by_gene_cells(region):
    """Source A: region x gene mean per-cell counts from the cell-by-gene table.

    Returns (profile DataFrame [region_acronym x gene], per-region cell counts).
    """
    cbg_path = os.path.join(config.REGIONS[region], "cell_by_gene.csv")
    return region_by_gene_cells_paths(resolve_mapped(region), cbg_path)


def region_by_gene_cells_paths(mapped_csv, cbg_path):
    """Path-based variant of region_by_gene_cells for data that is NOT a config
    region (e.g. the web app's per-session upload dir). Same return contract."""
    mp = pd.read_csv(mapped_csv,
                     usecols=["EntityID", "allen_region_id", "allen_acronym"])
    cbg = pd.read_csv(cbg_path)
    idcol = cbg.columns[0]
    cbg = cbg.rename(columns={idcol: "EntityID"})
    genes = [c for c in cbg.columns
             if c != "EntityID" and not c.lower().startswith("blank")]
    df = mp.merge(cbg[["EntityID"] + genes], on="EntityID", how="inner")
    df = df[df["allen_region_id"] > 0]
    grp = df.groupby("allen_acronym")
    n_cells = grp.size().rename("n_cells")
    prof = grp[genes].mean()
    keep = n_cells[n_cells >= MIN_CELLS].index
    return prof.loc[keep], n_cells.loc[keep]


def _sample_annotation_on_slice(region=None, *, fit_path=None, exp_path=None,
                                angle=0, flip=None):
    """Reload a registration fit and reproduce the density-grid geometry and
    Allen annotation resampled onto the fitted plane.

    Config path: pass region (uses resolve_fit + config.REGIONS + orient_xy).
    Web/upload path: pass fit_path + exp_path + (angle, flip) instead of region.

    Returns (AphiL, r0, c0, drow, dcol, transpose, acro) where AphiL[row,col] is
    the Allen region id a cell/transcript at density-grid pixel (row,col) gets, and
    acro(id)->acronym. CPU-only (single slice, fast) so it does not touch the GPU
    a reviewer may be using.
    """
    from brainglobe_atlasapi import BrainGlobeAtlas
    from .. import raster

    fit = load_fit(fit_path or resolve_fit(region))
    A, xI, dx = fit["A"], fit["xI"], fit["dx"]

    # --- rebuild the density grid from the SAME cells m3 used (rasterize is
    #     deterministic given the same points + dx), applying the same orientation
    #     (orient_xy is a no-op for regions without an _ORIENT entry). ---
    meta = pd.read_csv(os.path.join(exp_path or config.REGIONS[region],
                                    "cell_metadata.csv"),
                       usecols=["center_x", "center_y"])
    if region is not None:
        cx, cy = config.orient_xy(meta["center_x"].to_numpy(),
                                  meta["center_y"].to_numpy(), region)
        transpose = config._TRANSPOSE.get(region, False)
    else:
        cx, cy = config.orient_apply(meta["center_x"].to_numpy(),
                                     meta["center_y"].to_numpy(), angle, flip)
        transpose = False
    X, Y, _ = raster.density_raster(cx, cy, dx)
    if transpose:
        drow, dcol, r0, c0 = X[1] - X[0], Y[1] - Y[0], X[0], Y[0]
        xJ = [X - X.mean(), Y - Y.mean()]
    else:
        drow, dcol, r0, c0 = Y[1] - Y[0], X[1] - X[0], Y[0], X[0]
        xJ = [Y - Y.mean(), X - X.mean()]

    # --- resample the Allen annotation along the fitted plane (as analyze3Dalign) ---
    atlas = BrainGlobeAtlas(fit["atlas"], check_latest=False)
    XJg = np.stack(np.meshgrid(np.zeros(1), xJ[0], xJ[1], indexing="ij"), -1)
    # A is the silhouette-seeded FORWARD affine, sampled exactly as
    # register_region samples it
    pts = XJg @ A[:3, :3].T + A[:3, 3]   # forward affine: J-plane -> atlas um
    AphiL = raster.sample_nearest(xI, atlas.annotation, pts)[0].astype(np.int32)

    def acro(i):
        try:
            return atlas.structures[int(i)]["acronym"]
        except Exception:
            return None

    return AphiL, float(r0), float(c0), float(drow), float(dcol), transpose, acro


def region_by_gene_transcripts(region=None, split_half=False, seed=0, min_tx=1000,
                               batch_rows=2_000_000, normalize=True,
                               fit_path=None, exp_path=None, angle=0, flip=None):
    """Source B -- segmentation-free region x gene profile from detected_transcripts.

    Reuses the m3 fit (no re-optimization): sample the Allen annotation on the
    fitted plane, then stream detected_transcripts.(parquet|csv), map each
    transcript's (global_x, global_y) onto the SAME density grid m3 used, look up
    its Allen region, and accumulate a gene x region count table. Row-normalized to
    per-region transcript fractions (x1e4) so region size is not a confound.

    Config path: pass region. Web/upload path: pass fit_path + exp_path + (angle,
    flip). Returns (profile [acronym x gene], per-region transcript totals). If
    split_half, returns ((profA, profB), totals) -- two independent halves.
    """
    import pyarrow.parquet as pq

    AphiL, r0, c0, drow, dcol, transpose, acro = _sample_annotation_on_slice(
        region, fit_path=fit_path, exp_path=exp_path, angle=angle, flip=flip)
    H, W = AphiL.shape
    rng = np.random.default_rng(seed)

    tx_dir = exp_path or config.REGIONS[region]
    tx_path_pq = os.path.join(tx_dir, "detected_transcripts.parquet")
    tx_path_csv = os.path.join(tx_dir, "detected_transcripts.csv")
    cols = ["gene", "global_x", "global_y"]

    # Resolve orientation. Web/upload path uses the given (angle, flip); config
    # path looks up the region's stored orientation. Whenever there IS an
    # orientation, cells and transcripts must share ONE origin (the cell rot-flip
    # min) -- shifting each to its own min misaligns them by a few px (see the note
    # in config.orient_rotflip). Non-oriented config regions stay a pure no-op.
    if region is None:
        ang, flp = angle, flip
    else:
        o = config._ORIENT.get(region)
        ang, flp = o if o is not None else (None, None)
    oriented = (ang is not None) or (flp is not None)
    smin = None
    if oriented:
        cmeta = pd.read_csv(os.path.join(tx_dir, "cell_metadata.csv"),
                            usecols=["center_x", "center_y"])
        crx, cry = config.orient_rotflip(cmeta["center_x"].to_numpy(),
                                         cmeta["center_y"].to_numpy(), ang, flp)
        smin = (crx.min(), cry.min())

    def batches():
        if os.path.exists(tx_path_pq):
            for b in pq.ParquetFile(tx_path_pq).iter_batches(batch_size=batch_rows,
                                                             columns=cols):
                yield b.to_pandas()
        elif os.path.exists(tx_path_csv):
            for b in pd.read_csv(tx_path_csv, usecols=cols, chunksize=batch_rows):
                yield b
        else:
            raise FileNotFoundError(
                f"detected_transcripts.(parquet|csv) not found in {tx_dir}")

    accA, accB = [], []
    ntx = 0
    for df in batches():
        if oriented:
            rx, ry = config.orient_rotflip(df["global_x"].to_numpy(),
                                           df["global_y"].to_numpy(), ang, flp)
            gx, gy = rx - smin[0], ry - smin[1]
        else:
            gx, gy = df["global_x"].to_numpy(), df["global_y"].to_numpy()
        rsrc, csrc = (gx, gy) if transpose else (gy, gx)
        row = np.round((rsrc - r0) / drow).astype(np.int64)
        col = np.round((csrc - c0) / dcol).astype(np.int64)
        inb = (row >= 0) & (row < H) & (col >= 0) & (col < W)
        rid = np.zeros(len(df), dtype=np.int64)
        rid[inb] = AphiL[row[inb], col[inb]]
        keep = rid > 0
        sub = pd.DataFrame({"gene": df["gene"].to_numpy()[keep], "rid": rid[keep]})
        ntx += len(sub)
        if split_half:
            h = rng.random(len(sub)) < 0.5
            accA.append(sub[h].groupby(["rid", "gene"]).size())
            accB.append(sub[~h].groupby(["rid", "gene"]).size())
        else:
            accA.append(sub.groupby(["rid", "gene"]).size())

    def build(acc):
        s = pd.concat(acc).groupby(level=[0, 1]).sum()      # (rid, gene) -> count
        mat = s.unstack(fill_value=0)                        # rid x gene
        mat.index = [acro(i) for i in mat.index]
        mat = mat[[a is not None for a in mat.index]]
        mat = mat.groupby(level=0).sum()                     # merge any shared acronym
        totals = mat.sum(axis=1)
        mat = mat.loc[totals[totals >= min_tx].index]
        totals = totals.loc[mat.index]
        if not normalize:
            return mat, totals                               # RAW region x gene counts
        frac = mat.div(totals, axis=0) * 1e4                 # per-region fraction (x1e4)
        return frac, totals

    print(f"[transcripts] region={region}: {ntx:,} transcripts landed in a region")
    if split_half:
        fA, nA = build(accA); fB, nB = build(accB)
        return (fA, fB), (nA + nB)
    return build(accA)


def load_allen_reference(path):
    """Allen region x gene reference. CSV: first column = region key
    (Allen acronym, matching allen_acronym in the mapped CSV), remaining columns =
    genes. Values = expression (ISH energy / mean scRNA counts / etc.)."""
    from allen_alignment.reference_files import require
    ref = pd.read_csv(require(path), index_col=0)
    ref = ref[ref.index.notna()]                 # drop any NaN-acronym structure
    ref.index = ref.index.astype(str)
    return ref


# ------------------------------------------------------------------- metric
def _zscore_genes(prof):
    """log1p then z-score EACH gene across regions: compares regional specificity
    patterns, not overall abundance (housekeeping genes would otherwise dominate)."""
    x = np.log1p(prof.values.astype(float))
    mu = np.nanmean(x, 0, keepdims=True)          # ignore NaN (Allen ref ~4% gaps)
    sd = np.nanstd(x, 0, keepdims=True)
    sd[(sd == 0) | ~np.isfinite(sd)] = 1.0
    z = (x - mu) / sd
    z = np.nan_to_num(z, nan=0.0)                  # missing gene -> mean (0 in z-space)
    return pd.DataFrame(z, index=prof.index, columns=prof.columns)


def cross_correlation(P, Q):
    """Region-by-region Pearson correlation across SHARED genes.
    P, Q are region x gene DataFrames. Returns corr matrix [P.regions x Q.regions].
    """
    genes = [g for g in P.columns if g in Q.columns]
    if not genes:
        raise ValueError("no shared genes between the two profiles")
    Pz = _zscore_genes(P[genes]).values
    Qz = _zscore_genes(Q[genes]).values
    # Pearson across genes for every region pair = cosine of row-centered vectors
    Pz = Pz - Pz.mean(1, keepdims=True)
    Qz = Qz - Qz.mean(1, keepdims=True)
    Pz /= (np.linalg.norm(Pz, axis=1, keepdims=True) + 1e-12)
    Qz /= (np.linalg.norm(Qz, axis=1, keepdims=True) + 1e-12)
    C = Pz @ Qz.T
    return pd.DataFrame(C, index=P.index, columns=Q.index), genes


def diagonal_scores(C):
    """For each row region present in both axes: self-correlation, best-match
    region, and whether the best match is itself (the key QC pass/fail)."""
    rows = []
    for r in C.index:
        self_c = C.loc[r, r] if r in C.columns else np.nan
        best = C.loc[r].idxmax()
        rows.append((r, self_c, best, C.loc[r, best], best == r))
    return pd.DataFrame(rows, columns=["region", "self_corr", "best_match",
                                       "best_corr", "self_is_best"])


# ------------------------------------------------------------------- plotting
def plot_heatmap(C, title, out):
    idx = list(C.index)[:TOP_REGIONS]
    col = [c for c in idx if c in C.columns] or list(C.columns)[:TOP_REGIONS]
    sub = C.loc[idx, col]
    fig, ax = plt.subplots(figsize=(1 + 0.4 * len(col), 1 + 0.4 * len(idx)))
    im = ax.imshow(sub.values, vmin=-1, vmax=1, cmap="RdBu_r", aspect="auto")
    ax.set_xticks(range(len(col))); ax.set_xticklabels(col, rotation=90, fontsize=7)
    ax.set_yticks(range(len(idx))); ax.set_yticklabels(idx, fontsize=7)
    ax.set_xlabel("reference region"); ax.set_ylabel("MERFISH mapped region")
    ax.set_title(title, fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.03, label="expr correlation")
    fig.tight_layout(); fig.savefig(out, dpi=130); plt.close(fig)


# ------------------------------------------------------------------- modes
def run_selfcheck(region, seed=0):
    """No Allen data: split each region's cells in half, cross-correlate the two
    halves. Validates the metric + normalization -- a working method must give a
    strong diagonal here (a region's two halves are the same tissue)."""
    mp = pd.read_csv(resolve_mapped(region),
                     usecols=["EntityID", "allen_region_id", "allen_acronym"])
    cbg = pd.read_csv(os.path.join(config.REGIONS[region], "cell_by_gene.csv"))
    cbg = cbg.rename(columns={cbg.columns[0]: "EntityID"})
    genes = [c for c in cbg.columns
             if c != "EntityID" and not c.lower().startswith("blank")]
    df = mp.merge(cbg[["EntityID"] + genes], on="EntityID", how="inner")
    df = df[df["allen_region_id"] > 0]
    rng = np.random.default_rng(seed)
    half = rng.random(len(df)) < 0.5
    def prof(mask):
        g = df[mask].groupby("allen_acronym")
        n = g.size()
        p = g[genes].mean()
        return p.loc[n[n >= MIN_CELLS].index]
    P, Q = prof(half), prof(~half)
    common = [r for r in P.index if r in Q.index]
    P, Q = P.loc[common], Q.loc[common]
    C, shared = cross_correlation(P, Q)
    sc = diagonal_scores(C)
    out_png = os.path.join(config.outdir("m4_output"),
                           f"m4_expr_corr_{region}_selfcheck.png")
    plot_heatmap(C, f"{region}: split-half self-consistency ({len(shared)} genes)", out_png)
    return C, sc, out_png


def run_allen(region, allen_path, source="cells"):
    P, n = (region_by_gene_transcripts(region) if source == "transcripts"
            else region_by_gene_cells(region))
    A = load_allen_reference(allen_path)
    # Restrict BOTH sides to the regions present in our section before the metric.
    # cross_correlation z-scores each gene ACROSS regions, so if A keeps all ~707
    # whole-brain regions its per-gene baseline is set by regions not in our section
    # and the diagonal collapses. Aligning region sets makes the specificity patterns
    # comparable (this is the fix that turned 0/57 -> a real diagonal).
    common = [r for r in P.index if r in A.index]
    P, A = P.loc[common], A.loc[common]
    C, shared = cross_correlation(P, A)
    sc = diagonal_scores(C)
    od = config.outdir("m4_output")
    P.to_csv(os.path.join(od, f"m4_expr_corr_{region}_{source}_regionbygene.csv"))
    sc.to_csv(os.path.join(od, f"m4_expr_corr_{region}_{source}_scores.csv"), index=False)
    out_png = os.path.join(od, f"m4_expr_corr_{region}_{source}_heatmap.png")
    plot_heatmap(C, f"{region}: MERFISH ({source}) vs Allen ({len(shared)} genes)", out_png)
    return C, sc, out_png


def run_transcripts_selfcheck(region, seed=0):
    """No Allen data, Source B: split the transcripts of each region in half and
    cross-correlate the two halves -- validates the segmentation-free profile
    machinery (a region's two transcript halves are the same tissue)."""
    (P, Q), _ = region_by_gene_transcripts(region, split_half=True, seed=seed)
    common = [r for r in P.index if r in Q.index]
    P, Q = P.loc[common], Q.loc[common]
    C, shared = cross_correlation(P, Q)
    sc = diagonal_scores(C)
    out_png = os.path.join(config.outdir("m4_output"),
                           f"m4_expr_corr_{region}_transcripts_selfcheck.png")
    plot_heatmap(C, f"{region}: transcript split-half self-consistency "
                    f"({len(shared)} genes)", out_png)
    return C, sc, out_png


def run_compare_ab(region):
    """No Allen data: cross-correlate the cell-based (A) vs transcript-based (B)
    region profiles. A strong diagonal means the segmentation-free transcript
    modality (the stated validation channel) reproduces the cell biology
    -- the key check that Source B is trustworthy before Allen ground truth lands."""
    A, _ = region_by_gene_cells(region)
    B, _ = region_by_gene_transcripts(region)
    common = [r for r in A.index if r in B.index]
    A, B = A.loc[common], B.loc[common]
    C, shared = cross_correlation(A, B)          # rows = cells (A), cols = transcripts (B)
    sc = diagonal_scores(C)
    out_png = os.path.join(config.outdir("m4_output"),
                           f"m4_expr_corr_{region}_AvsB.png")
    plot_heatmap(C, f"{region}: cells (A) vs transcripts (B) "
                    f"({len(shared)} genes, {len(common)} regions)", out_png)
    return C, sc, out_png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default=config.REGION)
    ap.add_argument("--allen", default=None,
                    help="Allen region x gene reference CSV; omit for self-check")
    ap.add_argument("--source", default="cells", choices=["cells", "transcripts"])
    ap.add_argument("--compare_ab", action="store_true",
                    help="cross-correlate cell (A) vs transcript (B) profiles "
                         "(no Allen ref needed) -- validates the transcript path")
    args = ap.parse_args()

    if args.compare_ab:
        C, sc, png = run_compare_ab(args.region)
        mode = "cells(A)-vs-transcripts(B)"
    elif args.allen:
        C, sc, png = run_allen(args.region, args.allen, source=args.source)
        mode = f"MERFISH({args.source})-vs-Allen"
    elif args.source == "transcripts":
        C, sc, png = run_transcripts_selfcheck(args.region)
        mode = "transcript split-half self-consistency"
    else:
        C, sc, png = run_selfcheck(args.region)
        mode = "split-half self-consistency"

    npass = int(sc["self_is_best"].sum())
    print(f"\n=== expr-correlation QC ({mode}) region={args.region} ===")
    print(f"regions scored: {len(sc)}   self-is-best: {npass}/{len(sc)}"
          f"   median self-corr: {sc['self_corr'].median():.3f}")
    print(sc.sort_values("self_corr", ascending=False).head(15).to_string(index=False))
    print("\nheatmap ->", png)


if __name__ == "__main__":
    from scripts.registration import rtlog
    with rtlog.stage("m4_expr_corr", region=config.REGION):
        main()
