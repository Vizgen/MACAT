#!/usr/bin/env python
"""Build the SECTION x gene references from the Allen ABC MERFISH release.

Two tables, one per section plane, from one pass over the expression matrix.

Companion to fetch_abc_reference.py, which builds a REGION x gene table for the
per-region specificity QC. This one answers a different question: does the whole
section look like brain tissue at this AP at all? That is a tissue-level sanity
check, and it needs a whole-slide profile, not a per-region one.

WHY A SECTION AXIS AND NOT THE WHOLE-BRAIN MEAN
-----------------------------------------------
A whole-brain average is not a brain slice. A cerebellar section and a forebrain
section have very different global expression, so correlating a query section
against the brain-wide mean would score every section against tissue it never
contained. The ABC release carries `brain_section_label` per cell, and CCF
coordinates, so each of its 53 sections can be placed on the same AP axis this
app already uses -- and the query compared against the ONE that matches its AP.

The AP axis is `x_ccf`: within a section its spread is ~0.17 mm against 3.35 mm
between section means, a 20:1 ratio, while y and z are the other way round.
Section NUMBERS are the release's own acquisition index -- they run backwards
(higher number = more anterior) and are not evenly spaced (median 184 um, range
73-772 um), so they must be converted through the CCF coordinate rather than
assumed to be slice indices.

PROVENANCE -> docs/reference_provenance.md. Same release, same two files, same
RAW-not-log2 trap, as fetch_abc_reference.py.

SAGITTAL IS A RE-SLICE, NOT A SECOND DATASET
--------------------------------------------
The release was acquired as 53 coronal sections, but every cell carries full CCF
coordinates, so it is a 3-D point cloud and can be re-sliced along any axis. A
sagittal reference slab is every cell whose `z_ccf` (the ML axis) falls in a
200 um band, pooled across all 53 coronal sections.

That pooling needs a correction. The coronal sections are 73-772 um apart -- an
8.6x range in the AP thickness each one stands in for -- so a naive pool
over-weights the densely sampled APs. Measured: 11.5% of the profile's mass sits
on the wrong AP without it. Each cell is therefore weighted by the AP width its
section represents (half the gap either side), which is what a real sagittal
section would integrate.

The coronal table needs no such weighting: there the section IS the sample.

OUTPUT
------
data/reference/allen_abc_section_by_gene.csv        (coronal)
    index   brain_section_label  ("C57BL6J-638850.37")
    ap_mm   the section's mean CCF AP coordinate
    n_cells cells behind the profile
    <gene>  mean raw counts per cell

data/reference/allen_abc_sagittal_by_gene.csv       (sagittal)
    index   ml_bin  ("ML_5.70")
    ml_mm   the band's centre on the CCF ML axis
    n_cells cells behind the profile
    <gene>  AP-thickness-weighted mean raw counts per cell

Both axes share the atlas frame: a slice index converts as
`mm = idx * ATLAS_RES_UM / 1000`, measured from the anterior pole (AP, axis 0)
or the right edge (ML, axis 2).

Run:  conda run -n allen_reference python scripts/data/fetch_abc_section_reference.py
"""
import os
import sys

_pre = os.path.join(sys.prefix, "lib", "libstdc++.so.6")
if os.path.exists(_pre) and _pre not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = _pre + (
        ":" + os.environ["LD_PRELOAD"] if os.environ.get("LD_PRELOAD") else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)

import h5py                      # noqa: E402
import numpy as np               # noqa: E402
import pandas as pd              # noqa: E402
import scipy.sparse as sp        # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDY = os.path.dirname(os.path.dirname(_HERE))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)
import config                    # noqa: E402

ABC = os.environ.get("ALLEN_ABC_SOURCE_DIR") or os.path.join(
    config.DATA_DIR, "abc_source")
H5AD = os.path.join(ABC, "scratch/C57BL6J-638850-raw_no_blank.h5ad")
VIEW = os.path.join(ABC, "abc_atlas/metadata/MERFISH-C57BL6J-638850-CCF/"
                         "20231215/views/cell_metadata_with_parcellation_annotation.csv")
# see fetch_abc_reference.py: ALLEN_REFERENCE_OUT builds the tables elsewhere
OUT_DIR = os.environ.get("ALLEN_REFERENCE_OUT") or config.REFERENCE_DATA_DIR
OUT = os.path.join(OUT_DIR, "allen_abc_section_by_gene.csv")
OUT_SAG = os.path.join(OUT_DIR, "allen_abc_sagittal_by_gene.csv")
os.makedirs(OUT_DIR, exist_ok=True)

def excluded_sections():
    """ALLEN_EXCLUDE_SECTIONS: comma-separated brain_section_labels to leave out.

    For the positive control: an Allen section scored by the QC must not also be
    in the reference it is scored against, or its score is partly a match of
    the section with itself. Unset (the default) builds the full reference,
    byte-identical to the committed one."""
    raw = os.environ.get("ALLEN_EXCLUDE_SECTIONS", "")
    return {s.strip() for s in raw.split(",") if s.strip()}

ML_BIN_UM = 200.0        # comparable to the coronal sections' 184 um median spacing

BLOCK = 250_000          # rows per chunk; keeps peak memory ~1 GB
MIN_CELLS = 5_000        # a section thinner than this is a partial slice


def assert_raw_counts(data):
    """Same guard as fetch_abc_reference.py, and for the same reason.

    The release ships `-raw.h5ad` and `-log2.h5ad` with identical shape, nnz and
    dtype, so they are byte-for-byte the same size and `ls` cannot tell them
    apart. Only the values can.
    """
    s = data[:200_000]
    if len(s) and not np.allclose(s, np.round(s)):
        raise SystemExit(
            f"expression values are not integers (min {s.min():.4f}, "
            f"max {s.max():.4f}) -- this looks like the log2 release.\n"
            f"Use C57BL6J-638850-raw.h5ad; see docs/reference_provenance.md.")


def main():
    for p in (H5AD, VIEW):
        if not os.path.exists(p):
            raise SystemExit(
                f"missing ABC source file: {p}\nSee docs/reference_provenance.md "
                f"for the release and the re-fetch recipe.")

    print("reading section labels + CCF coordinates ...")
    v = pd.read_csv(VIEW, usecols=["cell_label", "brain_section_label",
                                   "x_ccf", "z_ccf"],
                    dtype={"cell_label": str})
    drop = excluded_sections()
    if drop:
        print(f"  holding out {len(drop)} section(s): {', '.join(sorted(drop))}")
        v = v[~v["brain_section_label"].isin(drop)]
    v["cell_id"] = v["cell_label"].str.rsplit("-", n=1).str[0]
    v = v[~v["cell_id"].duplicated(keep="first")].set_index("cell_id")

    with h5py.File(H5AD, "r") as f:
        obs = pd.Index([x.decode() for x in f["obs"]["cell_label"][:]])
        vk = f["var"].attrs.get("_index", "_index")
        vk = vk.decode() if isinstance(vk, bytes) else vk
        genes = [x.decode() for x in f["var"][vk][:]]
        assert_raw_counts(f["X"]["data"])

    meta = v.reindex(obs)
    n_gene = len(genes)

    # ---- grouping 1: the real coronal sections ---------------------------
    cat = meta["brain_section_label"].astype("category")
    cor_codes = cat.cat.codes.to_numpy()
    cor_names = np.array(cat.cat.categories)
    ap = meta.groupby("brain_section_label", observed=True)["x_ccf"].mean()
    ap = ap.reindex(cor_names)

    # Each section stands in for half the AP gap on either side. Used to weight
    # the SAGITTAL pool so unevenly spaced sections do not tilt it; the coronal
    # table needs no weighting, because there the section is the sample.
    order = np.argsort(ap.to_numpy())
    a_sorted = ap.to_numpy()[order]
    edges = np.concatenate([[a_sorted[0]], (a_sorted[1:] + a_sorted[:-1]) / 2,
                            [a_sorted[-1]]])
    width_sorted = np.diff(edges)
    ap_width = np.empty(len(cor_names))
    ap_width[order] = width_sorted
    ap_width = ap_width / ap_width.mean()          # mean weight 1, for readability

    # ---- grouping 2: virtual sagittal slabs on the ML axis ----------------
    z = meta["z_ccf"].to_numpy(float)
    step = ML_BIN_UM / 1000.0
    with np.errstate(invalid="ignore"):
        sag_codes = np.where(np.isfinite(z), np.floor(z / step), -1).astype(int)
    sag_codes[(cor_codes < 0) | ~np.isfinite(z)] = -1       # need both to weight
    n_sag = int(sag_codes.max()) + 1 if (sag_codes >= 0).any() else 0
    sag_centres = (np.arange(n_sag) + 0.5) * step
    # a cell's weight is its SECTION's AP thickness
    wgt = np.where(cor_codes >= 0, ap_width[np.clip(cor_codes, 0, None)], 0.0)

    print(f"  {len(obs):,} cells, {(cor_codes >= 0).sum():,} placed | "
          f"{len(cor_names)} coronal sections, {n_sag} sagittal bands "
          f"of {ML_BIN_UM:.0f} um, {n_gene} genes")

    cor_sums = np.zeros((len(cor_names), n_gene)); cor_cnt = np.zeros(len(cor_names))
    sag_sums = np.zeros((n_sag, n_gene)); sag_w = np.zeros(n_sag)
    sag_cnt = np.zeros(n_sag)
    with h5py.File(H5AD, "r") as f:
        indptr = f["X"]["indptr"][:]
        data, indices = f["X"]["data"], f["X"]["indices"]
        for s in range(0, len(obs), BLOCK):
            e = min(s + BLOCK, len(obs))
            lo, hi = indptr[s], indptr[e]
            blk = sp.csr_matrix((data[lo:hi], indices[lo:hi], indptr[s:e + 1] - lo),
                                shape=(e - s, n_gene))
            idx = np.arange(e - s)

            c = cor_codes[s:e]; m = c >= 0
            if m.any():
                ind = sp.csr_matrix((np.ones(m.sum()), (c[m], idx[m])),
                                    shape=(len(cor_names), e - s))
                cor_sums += (ind @ blk).toarray()
                cor_cnt += np.asarray(ind.sum(1)).ravel()

            c = sag_codes[s:e]; m = c >= 0
            if m.any() and n_sag:
                w = wgt[s:e][m]
                ind = sp.csr_matrix((w, (c[m], idx[m])), shape=(n_sag, e - s))
                sag_sums += (ind @ blk).toarray()
                sag_w += np.asarray(ind.sum(1)).ravel()
                one = sp.csr_matrix((np.ones(m.sum()), (c[m], idx[m])),
                                    shape=(n_sag, e - s))
                sag_cnt += np.asarray(one.sum(1)).ravel()
            print(f"  {e:,}/{len(obs):,}", end="\r")
    print()

    def write(path, sums, denom, cnt, coord_name, coords, index_name, index, label):
        df = pd.DataFrame(sums / np.maximum(denom, 1e-12)[:, None],
                          index=index, columns=genes)
        df.insert(0, "n_cells", cnt.astype(int))
        df.insert(0, coord_name, coords)
        keep = (df["n_cells"] >= MIN_CELLS) & df[coord_name].notna()
        dropped = int((~keep).sum())
        df = df[keep].sort_values(coord_name)
        df.index.name = index_name
        df.to_csv(path)
        print(f"\nwrote {path}")
        print(f"  {df.shape[0]} {label} x {n_gene} genes"
              + (f"  ({dropped} dropped below {MIN_CELLS:,} cells)" if dropped else ""))
        print(f"  {coord_name} {df[coord_name].min():.2f} - "
              f"{df[coord_name].max():.2f} mm, {int(df['n_cells'].sum()):,} cells")

    write(OUT, cor_sums, cor_cnt, cor_cnt, "ap_mm", ap.to_numpy(),
          "brain_section_label", cor_names, "coronal sections")
    if n_sag:
        write(OUT_SAG, sag_sums, sag_w, sag_cnt, "ml_mm", sag_centres, "ml_bin",
              np.array([f"ML_{c:.2f}" for c in sag_centres]), "sagittal bands")


if __name__ == "__main__":
    main()
