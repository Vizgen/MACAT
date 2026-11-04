# `data/reference/` — what each file is, and the terms it is under

**This repository does not include the Allen-derived files below: you build
them (next section), and MACAT needs them to run.** The Apache License 2.0 that
covers the code does not cover them. They stay under the Allen Institute's terms, which
permit **noncommercial use only**. Reviewed 2026-10-19; see `NOTICE` at the
repository root and [`docs/reference_provenance.md`](../../docs/reference_provenance.md).

| file | source | license / terms | changes |
| --- | --- | --- | --- |
| `allen_abc_region_by_gene.csv` | Allen Brain Cell Atlas, `MERFISH-C57BL6J-638850` (expression `20230830`, CCF parcellation `20231215`) | **CC BY-NC 4.0** | per-cell counts aggregated to mean per CCF region (707 × 500), by `scripts/data/fetch_abc_reference.py` |
| `allen_abc_section_by_gene.csv` | same | **CC BY-NC 4.0** | aggregated per coronal section (53 × 500), by `scripts/data/fetch_abc_section_reference.py` |
| `allen_abc_sagittal_by_gene.csv` | same | **CC BY-NC 4.0** | aggregated per sagittal ML band (50 × 500), same script |
| `structure_tree_safe_2017.csv` | Allen CCFv3 (2017) ontology, as distributed by [cortex-lab/allenCCF](https://github.com/cortex-lab/allenCCF) | [Allen Institute Terms of Use](https://alleninstitute.org/terms-of-use/) | none (byte-identical copy) |
| `anatomical_markers.csv` | curated in this project (2026-09-14) | Apache-2.0, with the code | — |

## Rebuilding them, and checking a rebuild

Build them once, from Allen's public release, before starting MACAT:

```bash
# a separate environment, for building only: adds anndata + h5py (not in macat)
conda env create -f environment_allen_reference.yml
conda activate allen_reference
# ...or, without conda (the same pins):
#   uv venv --python 3.10 .venv-allen-reference
#   source .venv-allen-reference/bin/activate    # Windows: .venv-allen-reference\Scripts\activate
#   uv pip install -r requirements-allen-reference.txt

# 1. download ~9.2 GB from the public Allen Brain Cell Atlas bucket (no account
#    needed), drop the blank probes, and fetch the CCF ontology
python scripts/data/fetch_allen_sources.py

# 2. build the three expression tables from it
python scripts/data/fetch_abc_reference.py
python scripts/data/fetch_abc_section_reference.py
```

Downloads land in `data/abc_source/`, which is gitignored and can be deleted once
the tables are built. The tables are written to `data/reference/`. Two
environment variables change those locations:

- `ALLEN_ABC_SOURCE_DIR`: where to keep the download;
- `ALLEN_REFERENCE_OUT`: where to write the tables.

Re-running is safe: finished downloads are skipped and an interrupted one
resumes. Measured on a Linux server: about 10 minutes of download over parallel
connections, then under a minute per step, with a peak of **22 GB of RAM** while
dropping the blank probes (12.8 GB and 2.5 GB for the two builds). Disk peaks
at about **17 GB** (the 9.2 GB download plus a 7.5 GB copy without the blank
probes); the finished tables take under 10 MB, and `data/abc_source/` can be
deleted afterwards.

The same environment also builds the positive-control set
(`python scripts/data/build_positive_control.py`; see
[`../positive_control/README.md`](../positive_control/README.md)).

That route reproduced the original files byte for byte on 2026-10-19. Sources,
releases and the RAW-not-log2 check are in
[`docs/reference_provenance.md`](../../docs/reference_provenance.md). A rebuild
is correct if `sha256sum data/reference/*.csv` matches these:

| file | SHA-256 |
| --- | --- |
| `structure_tree_safe_2017.csv` | `d4c7e752df7917b3588d1d6d7876990e042f0b10c26d79b541b18b0a903403a7` |
| `allen_abc_region_by_gene.csv` | `713d32bfd288e470b8673167c480d5d3a852b83d989bf5df6357f8a8d8989e91` |
| `allen_abc_section_by_gene.csv` | `23e8789f140c4367c7bd6726e545f9a8b8afee259ff08acd6107957c0e11164e` |
| `allen_abc_sagittal_by_gene.csv` | `e89719063448d2d32c9eb39150c151c2eaed63c5bdf51fa071806ca64930ae76` |

## What the terms say

- **CC BY-NC 4.0** (stated on both ABC Atlas dataset pages, for
  `MERFISH-C57BL6J-638850` and `MERFISH-C57BL6J-638850-CCF`: "Data is being
  share under the CC BY NC 4.0 license"). Sharing and adapting are allowed,
  **with attribution**, **for noncommercial purposes only**, and changes must be
  indicated. <https://creativecommons.org/licenses/by-nc/4.0/>
- **Allen Institute Terms of Use** (updated 2022-05-05). Use is "for research or
  other noncommercial purposes"; derivatives ("Improvements") are allowed for
  noncommercial purposes; "You may not redistribute the Content or Improvements
  for commercial purposes without our written permission"; and proper citation
  per the Allen Citation Policy is required.

## How to attribute

> Allen Brain Cell Atlas, MERFISH-C57BL6J-638850, © Allen Institute for Brain
> Science, CC BY-NC 4.0. Yao, Z. et al. *Nature* 624, 317–332 (2023).
> <https://doi.org/10.1038/s41586-023-06812-z>

> Allen Mouse Brain Common Coordinate Framework v3, © Allen Institute for Brain
> Science. Wang, Q. et al. *Cell* 181, 936–953 (2020).
> <https://doi.org/10.1016/j.cell.2020.04.007>

This is a reading of the published terms, not legal advice. Whether Vizgen's use
counts as commercial, and whether the files may stay in a repository that is
distributed, is for Vizgen to decide, if necessary with the Allen Institute.
