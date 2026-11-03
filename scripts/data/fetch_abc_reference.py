#!/usr/bin/env python
"""
Build the Allen region x gene reference from the ABC Atlas MERFISH-C57BL6J-638850
dataset (chosen 2026-07-17) for m4_expr_corr.py --allen.

This is the MODALITY-MATCHED reference we wanted: whole-brain MERFISH (same assay
as our data), each cell carries a CCF parcellation label. We aggregate per-cell
raw counts to region-mean expression, at BOTH the CCF 'structure' and
'substructure' level (our sampled allen_acronym labels sit at a mix of levels),
and union them so the QC matches whatever overlaps.

PROVENANCE -> docs/reference_provenance.md, which names the exact Allen release,
the S3 bucket and the open licensing question. Read it before re-fetching.

  bucket       s3://allen-brain-cell-atlas/   (Allen abc_atlas_access client)
  expression   expression_matrices/MERFISH-C57BL6J-638850/20230830/
                   C57BL6J-638850-raw.h5ad    <- RAW, then blanks dropped
  parcellation metadata/MERFISH-C57BL6J-638850-CCF/20231215/views/
                   cell_metadata_with_parcellation_annotation.csv

THE RELEASE SHIPS A log2 FILE OF THE SAME NAME-SHAPE. It has identical dims,
identical nnz and identical dtype, so it is byte-for-byte the SAME SIZE and
cannot be told apart by `ls`. This script assumes RAW COUNTS -- it averages them,
and the QC then applies its own log1p before z-scoring, so feeding it the log2
file would log already-logged data and silently shift every correlation in
step 6. `assert_raw_counts` below checks rather than trusts.

  cell_label (strip -N suffix) -> h5ad obs id       (join)
  parcellation_structure / _substructure           (region label)
  raw_no_blank.h5ad  (4.33M cells x 500 genes)      (expression, gene symbols)
  -> region x gene mean counts -> allen_abc_region_by_gene.csv

Run from the repo root in the allen_reference environment (needs anndata),
after scripts/data/fetch_allen_sources.py -- see data/reference/README.md, "Rebuilding them":
    python scripts/data/fetch_abc_reference.py
"""
import os
import sys
import numpy as np
import pandas as pd
import scipy.sparse as sp
import anndata as ad

# Runnable as `python scripts/data/fetch_abc_reference.py` from the repo root,
# as README.md documents: put the repo on the path for `config`, the same way
# fetch_abc_section_reference.py already does.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
import config  # noqa: E402

# Root of the downloaded Allen ABC atlas release. These are ~8.5 GB of public
# Allen reference data, kept in-repo but gitignored (see .gitignore) so this
# script runs with no manual configuration. Derived from the repo root rather
# than hard-coded, so a checkout at any path works. ALLEN_ABC_SOURCE_DIR still
# overrides it for a host that keeps the download elsewhere.
ABC = os.environ.get("ALLEN_ABC_SOURCE_DIR") or os.path.join(
    config.DATA_DIR, "abc_source")
H5AD = os.path.join(ABC, "scratch/C57BL6J-638850-raw_no_blank.h5ad")
VIEW = os.path.join(ABC, "abc_atlas/metadata/MERFISH-C57BL6J-638850-CCF/"
                         "20231215/views/cell_metadata_with_parcellation_annotation.csv")
# ALLEN_REFERENCE_OUT writes the table somewhere other than data/reference/:
# a public checkout builds it in place, a verification run builds it aside.
OUT_DIR = os.environ.get("ALLEN_REFERENCE_OUT") or config.REFERENCE_DATA_DIR
OUT = os.path.join(OUT_DIR, "allen_abc_region_by_gene.csv")
os.makedirs(OUT_DIR, exist_ok=True)

def excluded_sections():
    """ALLEN_EXCLUDE_SECTIONS: comma-separated brain_section_labels to leave out.

    For the positive control: an Allen section scored by the QC must not also be
    in the reference it is scored against, or its score is partly a match of
    the section with itself. Unset (the default) builds the full reference,
    byte-identical to the committed one."""
    raw = os.environ.get("ALLEN_EXCLUDE_SECTIONS", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


for _p in (H5AD, VIEW):
    if not os.path.exists(_p):
        raise SystemExit(
            f"missing ABC source file: {_p}\n"
            f"The ~8.5 GB ABC download is gitignored, so a fresh clone does not "
            f"have it. Re-download from s3://allen-brain-cell-atlas/ with the "
            f"Allen abc_atlas_access client into {ABC}, or point "
            f"ALLEN_ABC_SOURCE_DIR at an existing copy.\n"
            f"Exact releases and the recipe: docs/reference_provenance.md")
MIN_CELLS = 50            # skip regions with too few cells (noisy profile)
DROP = {"", "unassigned"}  # non-structures; *-unassigned handled by suffix check


def assert_raw_counts(X, sample=200_000):
    """Refuse to build the reference from log-transformed expression.

    The release ships `-raw.h5ad` and `-log2.h5ad` with identical shape, identical
    non-zero count and identical dtype, so they are the same size on disk and a
    mix-up survives every check a human would casually make. The values are the
    only thing that distinguishes them: raw counts are integers, log2 values are
    not. Downstream this matters because the QC applies its own log1p, so the
    wrong file means logging already-logged data -- a silent shift in every
    correlation, with nothing in the output to reveal it.
    """
    d = X.data[:sample] if sp.issparse(X) else np.asarray(X).ravel()[:sample]
    if len(d) and not np.allclose(d, np.round(d)):
        raise SystemExit(
            f"expression values are not integers (sample min {d.min():.4f}, "
            f"max {d.max():.4f}, mean {d.mean():.4f}) -- this looks like the "
            f"log2 release, not raw counts.\n"
            f"Use C57BL6J-638850-raw.h5ad. See docs/reference_provenance.md, "
            f"'RAW, not log2 -- and why it matters'.")
    print(f"  raw-count check: sample min {d.min():.0f}, max {d.max():.0f}, "
          f"mean {d.mean():.2f}, all integers")


def region_means(codes, X, names):
    """Mean expression per region via a sparse one-hot indicator @ X.
    codes: int region code per cell (-1 = drop). Returns DataFrame region x gene."""
    keep = codes >= 0
    codes = codes[keep]
    Xk = X[keep]
    n_reg = len(names)
    ind = sp.csr_matrix((np.ones(len(codes)), (codes, np.arange(len(codes)))),
                        shape=(n_reg, Xk.shape[0]))
    sums = (ind @ Xk).toarray()                       # (n_reg, n_gene)
    cnt = np.asarray(ind.sum(1)).ravel()              # (n_reg,)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = sums / cnt[:, None]
    df = pd.DataFrame(mean, index=names)
    return df[cnt >= MIN_CELLS], cnt


def build_level(col, cell2lab, obs_ids, X, genes):
    lab = cell2lab[col].reindex(obs_ids)
    bad = lab.isna() | lab.isin(DROP) | lab.str.endswith("-unassigned", na=False)
    cat = lab.where(~bad, other=np.nan).astype("category")
    names = list(cat.cat.categories)
    codes = cat.cat.codes.to_numpy()                  # -1 where NaN/dropped
    df, cnt = region_means(codes, X, names)
    df.columns = genes
    print(f"  [{col}] {len(names)} regions, {int((codes>=0).sum()):,} cells labeled "
          f"-> {len(df)} regions >= {MIN_CELLS} cells")
    return df


def main():
    print("loading parcellation view ...")
    v = pd.read_csv(VIEW, usecols=["cell_label", "brain_section_label",
                                   "parcellation_structure",
                                   "parcellation_substructure"], dtype=str)
    drop = excluded_sections()
    if drop:
        print(f"  holding out {len(drop)} section(s): {', '.join(sorted(drop))}")
        v = v[~v["brain_section_label"].isin(drop)]
    v["cell_id"] = v["cell_label"].str.rsplit("-", n=1).str[0]
    cell2lab = v.set_index("cell_id")[["parcellation_structure",
                                       "parcellation_substructure"]]
    cell2lab = cell2lab[~cell2lab.index.duplicated(keep="first")]

    print("loading expression h5ad ...")
    a = ad.read_h5ad(H5AD)
    obs_ids = pd.Index(a.obs.index.astype(str))
    genes = list(a.var.index)
    X = a.X.tocsr() if sp.issparse(a.X) else sp.csr_matrix(a.X)
    print(f"  {X.shape[0]:,} cells x {X.shape[1]} genes")
    assert_raw_counts(X)

    frames = []
    for col in ["parcellation_substructure", "parcellation_structure"]:
        frames.append(build_level(col, cell2lab, obs_ids, X, genes))
    # union: substructure first; add structure-level rows not already present
    ref = frames[0]
    extra = frames[1].loc[[r for r in frames[1].index if r not in ref.index]]
    ref = pd.concat([ref, extra])
    ref.index.name = "acronym"
    ref.to_csv(OUT)
    print(f"\nwrote {OUT}  ({ref.shape[0]} regions x {ref.shape[1]} genes)")
    print("sample regions:", list(ref.index[:12]))


if __name__ == "__main__":
    main()
