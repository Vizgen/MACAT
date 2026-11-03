#!/usr/bin/env python
"""
ABC region-expression audit across the registered training regions (2026-07-20).

Biological QC that does NOT depend on the AP-slice GT (which is blocked on the
numbering fix). For each registered region, compare its mapped regions' expression
to the ABC MERFISH-638850 reference (allen_abc_region_by_gene.csv): a region whose
cells' expression best-correlates with ITS OWN Allen reference profile is mapped
consistently; one that best-matches a different region is expression-confusable
(a candidate mis-label / soft boundary).

Per region we report: how many mapped Allen regions were scored, the fraction
self-consistent (self-is-best), the median self-correlation, and the top
off-diagonal confusions (mapped -> best-matching-other). Sorted to a trust table.

Run:  conda run -n macat python m4_abc_audit.py            # all GT regions
      conda run -n macat python m4_abc_audit.py --region <region_key>  # one region
"""
import argparse
import os
import numpy as np
import pandas as pd

import config
from .m4_expr_corr import (region_by_gene_cells, region_by_gene_cells_paths,
                           load_allen_reference, cross_correlation, diagonal_scores)

GT = os.path.join(config.METADATA_DIR, "allen_slice_gt.csv")
ABC = os.path.join(config.REFERENCE_DATA_DIR, "allen_abc_region_by_gene.csv")
OUT = config.outdir("m4_output")

# Soft biological-QC gate on median self-correlation (AP-independent). Calibrated
# from §4.3: broken AP ~0.0; borderline 0.35-0.45; clean hippocampal 0.46-0.56.
GATE_WARN = 0.30


def _score(P, A, n_cells=None):
    """Core: score a region x gene profile P against ABC reference A.
    Returns (row_dict, detail_df) or None if too few shared regions.

    ``n_cells`` is the per-region cell count from region_by_gene_cells*; when
    given it is carried into the detail table, because how many cells a profile
    was built from changes how much weight a low score deserves."""
    common = [r for r in P.index if r in A.index]
    if len(common) < 5:
        return None
    C, shared = cross_correlation(P.loc[common], A.loc[common])
    sc = diagonal_scores(C)
    row = dict(
        n_regions=len(sc),
        self_is_best=int(sc["self_is_best"].sum()),
        frac_self_best=sc["self_is_best"].mean(),
        median_self_corr=sc["self_corr"].median(),
        frac_corr_gt0_3=(sc["self_corr"] > 0.3).mean(),
        n_genes=len(shared))
    # Two views of the same scoring. `scored` is EVERY region, which the web app
    # paginates; `detail` is the six worst failures, which is what the CLI prints
    # and what a one-line summary should show. Deriving both here keeps them from
    # disagreeing about the gap or the cell counts.
    sc = sc.copy()
    sc["gap"] = sc["best_corr"] - sc["self_corr"]
    if n_cells is not None:
        sc["n_cells"] = sc["region"].map(n_cells)
    row["scored"] = sc.sort_values("self_corr")
    detail = sc[~sc["self_is_best"]].sort_values("self_corr")[
        ["region", "self_corr", "best_match", "best_corr"]].head(6).copy()
    # The margin by which the other region won. Reading self_corr alone hides
    # the difference between a coin-flip (gap ~0.03, two plausible labels) and a
    # profile that genuinely looks like something else (gap ~0.70).
    detail["gap"] = detail["best_corr"] - detail["self_corr"]
    if n_cells is not None:
        detail["n_cells"] = detail["region"].map(n_cells)
    return row, detail


def audit_region(region, A):
    """Score ONE registered region's mapped labels vs the ABC reference.

    Returns (row_dict, detail_df) or None if the region can't be scored
    (missing mapped CSV / cell_by_gene, or too few shared regions)."""
    try:
        P, n = region_by_gene_cells(region)
    except Exception as e:
        print(f"  skip {region}: {e}")
        return None
    res = _score(P, A, n)
    if res is None:
        print(f"  skip {region}: too few shared regions")
        return None
    row, detail = res
    row = {"region": region, **row}
    return row, detail


def audit_paths(mapped_csv, cbg_path, abc=ABC):
    """Path-based single-region QC for data outside config.REGIONS (web app).

    Returns dict with median_self_corr / self_is_best / n_regions / n_genes /
    verdict ('PASS'/'WARN') and a 'confusions' DataFrame, or None on failure."""
    A = load_allen_reference(abc)
    P, n = region_by_gene_cells_paths(mapped_csv, cbg_path)
    res = _score(P, A, n)
    if res is None:
        return None
    row, detail = res
    row["verdict"] = "PASS" if row["median_self_corr"] >= GATE_WARN else "WARN"
    row["confusions"] = detail
    return row


def audit_one(region):
    """Single-region biological QC with a soft PASS/WARN gate (for the pipeline
    driver + web app). Prints a one-line verdict and returns the row dict."""
    A = load_allen_reference(ABC)
    res = audit_region(region, A)
    if res is None:
        print(f"QC {region}: SKIP (could not score)")
        return None
    row, detail = res
    msc = row["median_self_corr"]
    verdict = "PASS" if msc >= GATE_WARN else "WARN"
    print(f"QC {region}: median_self_corr={msc:.3f} "
          f"self_best={row['self_is_best']}/{row['n_regions']} "
          f"({row['n_genes']} genes) -> {verdict}")
    if verdict == "WARN":
        print("  low self-corr => likely AP/registration issue; check the overlay.")
        if len(detail):
            print("  top expression confusions (mapped -> best-matching-other):")
            print(detail.to_string(index=False, formatters={
                "self_corr": "{:.2f}".format, "best_corr": "{:.2f}".format}))
    return row


def main():
    A = load_allen_reference(ABC)
    gt = pd.read_csv(GT)
    rows, detail = [], {}
    for region in gt["region_key"]:
        res = audit_region(region, A)
        if res is None:
            continue
        row, det = res
        rows.append(row)
        detail[region] = det

    df = pd.DataFrame(rows).sort_values("median_self_corr", ascending=False)
    pd.set_option("display.width", 200)
    print(f"\n=== ABC region-expression audit ({len(gt)} training regions) ===")
    print("(median_self_corr = how well each mapped region matches its OWN ABC "
          "profile; higher = cleaner. frac_self_best harsh at fine sublayers.)\n")
    print(df.to_string(index=False, formatters={
        "frac_self_best": "{:.2f}".format, "median_self_corr": "{:.3f}".format,
        "frac_corr_gt0_3": "{:.2f}".format}))
    df.to_csv(os.path.join(OUT, "m4_abc_audit.csv"), index=False)

    # worst regions' top confusions (what they get mistaken for)
    print("\n--- top expression confusions in the 3 lowest-scoring sections ---")
    for region in df.tail(3)["region"]:
        print(f"\n{region}:")
        print(detail[region].to_string(index=False, formatters={
            "self_corr": "{:.2f}".format, "best_corr": "{:.2f}".format}))
    print(f"\ntable -> {os.path.join(OUT, 'm4_abc_audit.csv')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", help="score ONE region (pipeline QC gate) "
                    "instead of the full GT table")
    a = ap.parse_args()
    if a.region:
        audit_one(a.region)
    else:
        main()
