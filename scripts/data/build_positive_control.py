#!/usr/bin/env python
"""Build the positive-control set: MERFISH sections with KNOWN-CORRECT answers.

Every section here comes from a dataset in which each registered cell carries a
CCF registration made by its authors. Packaged as an ordinary experiment folder,
a section runs through MACAT exactly like a user's, and each answer the app gives
-- step 2 orientation, step 3 level, every cell's region -- can be scored
against the known one. Three sets (decided 2026-10-20):

  coronal, whole        Allen ABC MERFISH (MERFISH-C57BL6J-638850), 5 whole
                        bilateral sections. This is the dataset the QC
                        reference is built from, so the reference is rebuilt
                        WITHOUT them (reference_holdout/) for scoring.
                        CC BY-NC 4.0.
  coronal, hemisection  Zhuang-lab Zhuang-ABCA-1, 5 sections. The published
                        metadata has ONE hemisphere per coronal section, so
                        these test hemisection handling, which the automatic
                        fit does not do yet. CC BY 4.0.
  sagittal, whole       Zhuang-lab Zhuang-ABCA-3, 2 sections, one near-midline,
                        one lateral. CC BY 4.0.

    data/positive_control/
        sections.csv             manifest and answer key                 [tracked]
        README.md                                                        [tracked]
        sections/<name>/region_R1/   one app-ready experiment per section [ignored]
                                 (MERSCOPE layout: point MACAT's data folder at sections/)
            cell_metadata.csv    EntityID, center_x, center_y (um, section frame)
            cell_by_gene.csv     EntityID x genes (symbols), raw counts
            ground_truth.csv     registered cells: CCF x/y/z (mm), division,
                                 structure, substructure
            expected.json        that section's row of the manifest: its answer
                                 key, which MACAT reads (web/answer_key.py)
        reference_holdout/       the ABC tables without the Allen sections [ignored]

Sections are chosen by fixed rules (SETS below), so a rebuild picks the same
ones. Run from the repo root in the allen_reference environment:

    python scripts/data/build_positive_control.py
"""
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for p in (_REPO, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import config  # noqa: E402
import fetch_allen_sources as _fetch  # noqa: E402

ABC = os.path.join(os.environ.get("ALLEN_ABC_SOURCE_DIR")
                   or os.path.join(config.DATA_DIR, "abc_source"), "abc_atlas")
SCRATCH = os.path.join(os.path.dirname(ABC), "scratch")
OUT = config.POSITIVE_CONTROL_DIR
TERMS = "metadata/Allen-CCF-2020/20230630/views/parcellation_to_parcellation_term_membership_acronym.csv"
ALLEN_VIEW = ("metadata/MERFISH-C57BL6J-638850-CCF/20231215/views/"
              "cell_metadata_with_parcellation_annotation.csv")
ALLEN_H5AD = "C57BL6J-638850-raw_no_blank.h5ad"            # under SCRATCH

ML_MIDLINE_MM = 5.7
ATLAS_MM = {"coronal": 13.2, "sagittal": 11.4}            # along the slice axis
MIN_REGISTERED = 20000
REGION = "region_R1"          # each section is one region of its own "experiment"

SETS = [
    {"dataset": "MERFISH-C57BL6J-638850", "plane": "coronal", "type": "whole",
     "license": "CC BY-NC 4.0", "holdout": True,
     # whole, bilateral, spread front to back over distinct anatomy
     "sections": ["C57BL6J-638850.52", "C57BL6J-638850.45", "C57BL6J-638850.38",
                  "C57BL6J-638850.30", "C57BL6J-638850.14"]},
    {"dataset": "Zhuang-ABCA-1", "plane": "coronal", "type": "hemisection",
     "license": "CC BY 4.0", "holdout": False,
     "near_mm": [4.4, 5.9, 7.1, 8.8, 10.9]},                # AP from the anterior pole
    {"dataset": "Zhuang-ABCA-3", "plane": "sagittal", "type": "whole",
     "license": "CC BY 4.0", "holdout": False,
     "near_mm_from_midline": [2.5, 0.75]},
]


def zhuang_files(ds):
    return {"h5ad": f"expression_matrices/{ds}/20230830/{ds}-raw.h5ad",
            "meta": f"metadata/{ds}/20241115/cell_metadata.csv",
            "ccf": f"metadata/{ds}-CCF/20230830/ccf_coordinates.csv"}


def ensure_sources():
    """Download anything missing from the public bucket (skips what is present).
    The Allen MERFISH files come from scripts/data/fetch_allen_sources.py."""
    for s in SETS:
        if s["dataset"].startswith("Zhuang"):
            for k in zhuang_files(s["dataset"]).values():
                _fetch.download(f"{_fetch.BUCKET}/{k}", os.path.join(ABC, k),
                                parts=16 if k.endswith(".h5ad") else 8)
    _fetch.download(f"{_fetch.BUCKET}/{TERMS}", os.path.join(ABC, TERMS), parts=1)
    for p in (os.path.join(ABC, ALLEN_VIEW), os.path.join(SCRATCH, ALLEN_H5AD)):
        if not os.path.exists(p):
            raise SystemExit(f"missing {p}; run scripts/data/fetch_allen_sources.py first")


def name_for(s, label):
    sec = label.rsplit(".", 1)[1]
    if s["dataset"].startswith("MERFISH"):
        return f"pc_{s['plane']}_allen_{sec}"
    ds = s["dataset"].split("-", 1)[1].replace("-", "").lower()      # abca1
    hemi = "hemi_" if s["type"] == "hemisection" else ""
    return f"pc_{s['plane']}_{hemi}{ds}_{sec}"


# ----------------------------------------------------------------- loaders
# Each returns (cells, expression) for the chosen sections. `cells` has one row
# per cell: EntityID, section, sx/sy (section frame, mm), x_ccf/y_ccf/z_ccf (mm,
# NaN if unregistered), division/structure/substructure, and _row, its position
# in the expression matrix. `expression(rows)` returns (genes, counts).

def load_allen(s, terms):
    import h5py
    import scipy.sparse as sp
    v = pd.read_csv(os.path.join(ABC, ALLEN_VIEW), dtype={"cell_label": str}, usecols=[
        "cell_label", "brain_section_label", "x_section", "y_section", "x_ccf", "y_ccf",
        "z_ccf", "parcellation_division", "parcellation_structure",
        "parcellation_substructure"])
    v = v[v.brain_section_label.isin(s["sections"])].copy()
    v["EntityID"] = v.cell_label.str.rsplit("-", n=1).str[0]
    cells = v.drop_duplicates("EntityID").rename(columns={
        "brain_section_label": "section", "x_section": "sx", "y_section": "sy",
        "parcellation_division": "division", "parcellation_structure": "structure",
        "parcellation_substructure": "substructure"})
    cells["_order"] = cells.section.map({c: i for i, c in enumerate(s["sections"])})
    h5 = os.path.join(SCRATCH, ALLEN_H5AD)
    with h5py.File(h5, "r") as f:
        obs = pd.Index([c.decode() for c in f["obs"]["cell_label"][:]])
        genes = [g.decode() for g in f["var"][f["var"].attrs["_index"]][:]]
        X = sp.csr_matrix((f["X"]["data"][:], f["X"]["indices"][:], f["X"]["indptr"][:]),
                          shape=(len(obs), len(genes)))
    pos = obs.get_indexer(cells.EntityID)
    cells = cells[pos >= 0].assign(_row=pos[pos >= 0])
    return cells.sort_values("_order"), lambda rows: (genes, X[rows].toarray())


def load_zhuang(s, terms):
    import h5py
    f = {k: os.path.join(ABC, v) for k, v in zhuang_files(s["dataset"]).items()}
    meta = pd.read_csv(f["meta"], usecols=["cell_label", "brain_section_label", "x", "y"],
                       dtype={"cell_label": str})
    ccf = pd.read_csv(f["ccf"], dtype={"cell_label": str}).rename(
        columns={"x": "x_ccf", "y": "y_ccf", "z": "z_ccf"})
    cells = meta.merge(ccf, on="cell_label", how="left").merge(
        terms, on="parcellation_index", how="left").rename(columns={
            "cell_label": "EntityID", "brain_section_label": "section", "x": "sx", "y": "sy"})
    cells["s_mm"] = cells.x_ccf if s["plane"] == "coronal" else cells.z_ccf
    reg = cells.dropna(subset=["x_ccf"])
    pos = reg.groupby("section")["s_mm"].agg(["mean", "size"])
    pos = pos[pos["size"] >= MIN_REGISTERED]
    if "near_mm" in s:
        key, targets = pos["mean"], s["near_mm"]
    else:
        key, targets = (pos["mean"] - ML_MIDLINE_MM).abs(), s["near_mm_from_midline"]
    chosen = []
    for t in targets:
        chosen.append((key - t).abs().drop(chosen, errors="ignore").idxmin())
    cells = cells[cells.section.isin(chosen)].copy()
    cells["_order"] = cells.section.map({c: i for i, c in enumerate(chosen)})
    with h5py.File(f["h5ad"], "r") as h:
        obs = pd.Index([c.decode() for c in h["obs"][h["obs"].attrs["_index"]][:]])
        genes = [g.decode() for g in h["var"]["gene_symbol"][:]]
    pos = obs.get_indexer(cells.EntityID)
    cells = cells[pos >= 0].assign(_row=pos[pos >= 0])
    return cells.sort_values("_order"), lambda rows: (genes, read_dense_rows(f["h5ad"], rows))


def read_dense_rows(h5path, rows):
    """Rows of a dense X, read in sorted contiguous blocks."""
    import h5py
    order = np.argsort(rows)
    srt = np.asarray(rows)[order]
    with h5py.File(h5path, "r") as f:
        X = f["X"]
        out = np.empty((len(srt), X.shape[1]), dtype=np.float32)
        start = 0
        while start < len(srt):
            lo = srt[start]
            hi = min(lo + 20000, X.shape[0])
            stop = np.searchsorted(srt, hi)
            out[order[start:stop]] = X[lo:hi][srt[start:stop] - lo]
            start = stop
    return out


# ----------------------------------------------------------------- answers
def expected_orientation(sx, sy, horiz, dv):
    """(angle, flip) putting the section in the app's frame, and its score (2 =
    perfect). Every whole-degree angle and each flip step 2 offers, through
    config.orient_rotflip -- the transform the app bakes in. Target: image y
    with DV (dorsal on top), image x with the in-plane horizontal atlas axis
    increasing: ML for coronal, AP for sagittal (posterior to the right). For
    sagittal that fixes the answer; for coronal the left-right mirror scores the
    same and changes no region label (the annotation is symmetric)."""
    best = None
    for flip in (None, "x", "y"):
        for ang in range(360):
            x, y = config.orient_rotflip(sx, sy, float(ang), flip)
            sc = np.corrcoef(x, horiz)[0, 1] + np.corrcoef(y, dv)[0, 1]
            if best is None or sc > best[2]:
                best = (float(ang), flip, float(sc))
    return best


def write_section(s, label, c, expression):
    name = name_for(s, label)
    # <name>/region_R1/: the MERSCOPE <experiment>/region_* layout, so pointing
    # MACAT's data folder at sections/ lists every section (2026-10-28)
    d = os.path.join(OUT, "sections", name, REGION)
    os.makedirs(d, exist_ok=True)
    cm = pd.DataFrame({"EntityID": c.EntityID.to_numpy(),
                       "center_x": c.sx.to_numpy() * 1000.0,
                       "center_y": c.sy.to_numpy() * 1000.0})
    cm.to_csv(os.path.join(d, "cell_metadata.csv"), index=False)
    genes, X = expression(c["_row"].to_numpy())
    if not np.allclose(X, np.round(X)):
        raise ValueError(f"{label}: counts are not integers; is this a RAW matrix?")
    cbg = pd.DataFrame(np.round(X).astype(np.int64), columns=genes)
    cbg = cbg.T.groupby(level=0).sum().T                # a symbol listed twice -> one column
    cbg.insert(0, "EntityID", c.EntityID.to_numpy())
    cbg.to_csv(os.path.join(d, "cell_by_gene.csv"), index=False)

    reg = c.dropna(subset=["x_ccf"])
    reg[["EntityID", "x_ccf", "y_ccf", "z_ccf", "division", "structure",
         "substructure"]].to_csv(os.path.join(d, "ground_truth.csv"), index=False)

    plane = s["plane"]
    s_um = (reg.x_ccf if plane == "coronal" else reg.z_ccf).to_numpy() * 1000.0
    horiz = reg.z_ccf if plane == "coronal" else reg.x_ccf
    ang, flip, score = expected_orientation(reg.sx.to_numpy() * 1000.0,
                                            reg.sy.to_numpy() * 1000.0,
                                            horiz.to_numpy(), reg.y_ccf.to_numpy())
    exp = {"name": name, "section": label, "dataset": s["dataset"], "plane": plane,
           "section_type": s["type"], "license": s["license"],
           "held_out_of_reference": bool(s["holdout"]),
           "n_cells": int(len(c)), "n_registered": int(len(reg)),
           "slice_axis": "AP" if plane == "coronal" else "ML",
           "slice_mm": round(float(s_um.mean()) / 1000, 4),
           "slice_sd_mm": round(float(s_um.std()) / 1000, 4)}
    for res in (25, 50):
        # app index = microns along the slice axis / resolution, from the atlas
        # corner (docs/reference_provenance.md); tolerance = the section's own
        # spread along that axis (1 SD)
        idx = int(round(s_um.mean() / res))
        exp[f"idx_{res}um"] = idx
        exp[f"idx_{res}um_tolerance"] = max(1, int(np.ceil(s_um.std() / res)))
        if plane == "sagittal":           # the left-right mirror cuts the same anatomy
            exp[f"idx_{res}um_mirror"] = int(round(ATLAS_MM[plane] * 1000 / res)) - 1 - idx
    exp.update({"orientation_angle": ang, "orientation_flip": flip or "none",
                "orientation_score": round(score, 4)})
    # Which half to declare in step 2. With the orientation above, image x runs
    # with CCF ML, and the display shows the atlas's low-ML side on the left, so
    # a hemisphere below the midline is the LEFT half as displayed.
    exp["hemisection"] = (("left" if reg.z_ccf.mean() < ML_MIDLINE_MM else "right")
                          if s["type"] == "hemisection" else "")
    with open(os.path.join(d, "expected.json"), "w") as fh:
        json.dump(exp, fh, indent=2)
    print(f"  {name}: {len(c):,} cells ({len(reg):,} registered), {exp['slice_axis']} "
          f"{exp['slice_mm']:.2f} mm -> idx {exp['idx_25um']} @25um "
          f"(+/-{exp['idx_25um_tolerance']}), orientation {ang:.0f} deg "
          f"flip {flip or 'none'} (score {score:.3f})", flush=True)
    return exp


def main():
    ensure_sources()
    terms = pd.read_csv(os.path.join(ABC, TERMS))[
        ["parcellation_index", "division", "structure", "substructure"]]
    rows = []
    for s in SETS:
        print(f"[{s['dataset']}] {s['plane']} {s['type']} ...", flush=True)
        cells, expression = (load_allen if s["dataset"].startswith("MERFISH")
                             else load_zhuang)(s, terms)
        for label in dict.fromkeys(cells.section):
            rows.append(write_section(s, label, cells[cells.section == label], expression))
    man = pd.DataFrame(rows)
    for c in [c for c in man.columns if c.endswith("_mirror")]:
        man[c] = man[c].astype("Int64")       # only sagittal rows have one
    os.makedirs(OUT, exist_ok=True)
    man.to_csv(os.path.join(OUT, "sections.csv"), index=False)

    held = [r["section"] for r in rows if r["held_out_of_reference"]]
    print(f"rebuilding the QC reference without {len(held)} Allen sections ...", flush=True)
    env = dict(os.environ, ALLEN_EXCLUDE_SECTIONS=",".join(held),
               ALLEN_REFERENCE_OUT=os.path.join(OUT, "reference_holdout"))
    for script in ("fetch_abc_reference.py", "fetch_abc_section_reference.py"):
        subprocess.run([sys.executable, os.path.join(_HERE, script)], env=env, check=True)
    print(f"done: {len(rows)} sections -> {OUT}")


if __name__ == "__main__":
    main()
