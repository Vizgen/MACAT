"""Per-cell Allen labels as a table the MERSCOPE Vizualizer can import.

The per-cell deliverable (`cell_metadata_with_allen_regions*.csv`) is the whole
experiment `cell_metadata.csv` with Allen columns appended: 26 columns, ~51 MB a
section. The Vizualizer already has every non-Allen column, and a whole metadata
table with label columns appended crashed its import (2026-10-16). This writes
only what it needs to colour cells by region.

The format is the one built and import-tested in a notebook
(`MAAT_allen_regions_to_visualizer_v1.1.ipynb`, 2026-10-16), ported here as is,
including its checks; tests/test_vizualizer_export.py holds the port to that
notebook's output byte for byte. In short:

  * `EntityID`, which is how the Vizualizer identifies a cell, so no
    coordinates travel and none can drift;
  * `allen_region_id` as text (`id788`), so it cannot be read as a number;
  * `allen_acronym`, `allen_name`, `allen_parent`, `mapping_status`;
  * `ontology_level_1..N`, from `allen_ontology_path` with `root` (level 0)
    dropped. A path that stops early repeats its deepest region in the deeper
    columns, so every level colours the whole section;
  * every value categorical text of letters, digits, dashes and spaces.

One addition to the notebook: an unmapped cell whose atlas position lies past
the end of the volume is labelled `beyond atlas` rather than `unmapped`, when
the caller passes the atlas geometry. The reference stopping and the fit missing
are different findings, and the Results page already tells them apart
(`planes.atlas_overhang`); the export now does too.
"""
import os
import re

import numpy as np
import pandas as pd

SOURCE_PREFIX = "cell_metadata_with_allen_regions"
PREFIX = "TO_MERSCOPE_VIZUALIZER_allen_regions"
UNMAPPED = "unmapped"
BEYOND = "beyond atlas"
BASE_COLS = ["allen_region_id", "allen_acronym", "allen_name", "allen_parent",
             "mapping_status"]
_COORDS = ["atlas_ap_um", "atlas_si_um", "atlas_lr_um"]
_SAFE = re.compile(r"[^A-Za-z0-9\- ]")


def label(x):
    """A Vizualizer-safe label: letters, digits, dashes and single spaces.

    `/` becomes `-` FIRST, so `RSPv2/3` stays readable as `RSPv2-3` rather than
    `RSPv2 3`; then the spatial-transcriptomics skill's `visualizer_label`:
    `_` -> `-`, anything else outside the set -> space, whitespace collapsed.
    """
    x = str(x).replace("/", "-").replace("_", "-")
    x = re.sub(r"[^A-Za-z0-9\-]", " ", x)
    return re.sub(r"\s+", " ", x).strip()


def output_name(source_csv):
    """`cell_metadata_with_allen_regions<tag>.csv` -> `TO_MERSCOPE_..._regions<tag>.csv`.

    Named after its source, so a curated table carries `_curated` exactly when
    the CSV it came from does -- the same invariant `_derived_source` keeps for
    the region tables.
    """
    base = os.path.basename(source_csv)
    if not base.startswith(SOURCE_PREFIX):
        raise ValueError(f"not a per-cell Allen CSV: {base}")
    return PREFIX + base[len(SOURCE_PREFIX):]


def table(source_csv, atlas_shape=None, res=None):
    """The Vizualizer table for one per-cell CSV, indexed by `EntityID`.

    `atlas_shape` and `res` (the resolution the job was registered at) enable the
    `beyond atlas` category; without them the result is exactly the notebook's.
    Raises ValueError on anything the notebook asserted: duplicate or
    non-numeric ids, a mapped cell with no path, a path not ending at the cell's
    own acronym, two regions merged by cleaning, or a label that could be read
    as a number.
    """
    want = ["EntityID"] + BASE_COLS + ["allen_ontology_path"]
    head = pd.read_csv(source_csv, nrows=0).columns
    with_coords = atlas_shape is not None and all(c in head for c in _COORDS)
    src = pd.read_csv(source_csv, usecols=want + (_COORDS if with_coords else []),
                      dtype={"EntityID": str})
    if not src.EntityID.is_unique:
        raise ValueError("duplicate EntityID")
    if not src.EntityID.str.fullmatch(r"\d+").all():
        raise ValueError("an EntityID is not an integer id")
    src = src.set_index("EntityID")

    df = src[BASE_COLS].copy()
    unm = df.mapping_status.eq(UNMAPPED)
    if not (df.loc[unm, "allen_region_id"] == 0).all():
        raise ValueError("an unmapped cell carries a region id")
    df["allen_region_id"] = "id" + df.allen_region_id.astype(str)
    df[BASE_COLS[:4]] = df[BASE_COLS[:4]].astype(object)
    df.loc[unm, BASE_COLS[:4]] = UNMAPPED
    # `root` has no parent in the ontology; give it itself, not a blank
    root = df.allen_acronym.eq("root") & df.allen_parent.isna()
    df.loc[root, "allen_parent"] = "root"
    if df.isna().any().any():
        raise ValueError("unexpected missing value in "
                         + ", ".join(df.columns[df.isna().any()]))

    # ---- allen_ontology_path -> one column per level, root dropped
    path = src.allen_ontology_path
    if not path.isna().equals(unm):
        raise ValueError("ontology path missing for a mapped cell, or present "
                         "for an unmapped one")
    parts = path[~unm].str.split(">")
    if not (parts.str[0].eq("root").all()
            and (parts.str[-1] == src.allen_acronym[~unm]).all()):
        raise ValueError("an ontology path does not run root -> the cell's acronym")
    parts = parts.str[1:]
    n_lev = int(parts.str.len().max()) if len(parts) else 0
    deepest = parts.map(lambda p: p[-1] if p else "root")
    levels = [f"ontology_level_{k}" for k in range(1, n_lev + 1)]
    for k, col in enumerate(levels):
        lev = parts.map(lambda p, k=k: p[k] if k < len(p) else None).fillna(deepest)
        df[col] = UNMAPPED
        df.loc[lev.index, col] = lev
    cols = BASE_COLS + levels

    # ---- unmapped because the reference ends, not because the fit missed
    if with_coords:
        from .planes import beyond_atlas
        out, _ = beyond_atlas(src[_COORDS].to_numpy(float), atlas_shape, res)
        out = pd.Series(out, index=src.index) & unm
        df.loc[out, cols] = BEYOND

    # ---- sanitise, and refuse to merge two regions into one label
    clean = df[cols].apply(lambda s: s.map(label))
    for c in cols:
        if df[c].nunique() != clean[c].nunique():
            raise ValueError(f"{c}: cleaning would merge two different labels")
    if not (df.allen_region_id.groupby(df.allen_acronym).nunique() == 1).all():
        raise ValueError("an acronym maps to more than one region id")
    if clean.apply(lambda s: s.str.contains(_SAFE)).any().any():
        raise ValueError("a label still holds a character the Vizualizer rejects")
    if clean.apply(lambda s: pd.to_numeric(s, errors="coerce").notna().any()).any():
        raise ValueError("a column could be read as numeric")
    clean.index.name = "EntityID"
    return clean


def write(source_csv, out_path, atlas_shape=None, res=None):
    """Write the table atomically, so a half-written file is never served.
    Returns {"path", "n_cells", "columns", "n_categories", "n_beyond"}."""
    t = table(source_csv, atlas_shape=atlas_shape, res=res)
    tmp = out_path + ".tmp"
    t.to_csv(tmp)
    os.replace(tmp, out_path)
    return {"path": out_path, "n_cells": int(len(t)),
            "columns": ["EntityID"] + list(t.columns),
            "n_categories": {c: int(t[c].nunique()) for c in t.columns},
            "n_beyond": int(t.mapping_status.eq(BEYOND).sum())}
