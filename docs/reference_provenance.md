# Where the Allen ABC reference came from

`data/reference/allen_abc_region_by_gene.csv` is the only reference the
expression QC scores against, and it is the one tracked file in this repo
derived from an external dataset. This page says exactly which Allen release it
came from, so a run can be reproduced and so the licensing review has something
to review.

Written 2026-09-22, after tracing the file back to its source.

## The short version

| | |
| --- | --- |
| Dataset | Allen Brain Cell Atlas, **`MERFISH-C57BL6J-638850`** (whole mouse brain MERFISH, 4,334,174 cells × 500 genes) |
| Bucket | `s3://allen-brain-cell-atlas/` |
| Access | the Allen `abc_atlas_access` package, `AbcProjectCache.from_cache_dir(...)` |
| Expression | `expression_matrices/MERFISH-C57BL6J-638850/20230830/C57BL6J-638850-raw.h5ad` |
| Parcellation | `metadata/MERFISH-C57BL6J-638850-CCF/20231215/views/cell_metadata_with_parcellation_annotation.csv` |
| Cache manifest | release `20251031` |
| Licence | **CC BY-NC 4.0** (noncommercial; attribution required). Reviewed 2026-10-19, see *Licence* below |
| Derived tables | `allen_abc_region_by_gene.csv` (707 x 500), `allen_abc_section_by_gene.csv` (53 x 500, coronal), `allen_abc_sagittal_by_gene.csv` (50 x 500, ML bands) |

## Why this dataset

It is **modality-matched**. The thing being QC'd is MERFISH, and this reference
is whole-brain MERFISH on the same 500-gene panel, with every cell carrying a
CCF parcellation label. A single-cell RNA-seq reference would have compared
across assays as well as across regions, which confounds the measurement the QC
is trying to make.

That is also why it is *not* an independent positive control: same assay, same
panel, same institute.

## The exact chain

1. **Downloaded** with the Allen Institute's own client, installed from
   `git+https://github.com/alleninstitute/abc_atlas_access.git`, which pulls
   from `s3://allen-brain-cell-atlas/`. Docs:
   <https://alleninstitute.github.io/abc_atlas_access/notebooks/getting_started.html>

2. **Blank probes dropped.** The released `C57BL6J-638850-raw.h5ad` carries the
   panel's blank/control probes alongside the real genes. They were removed to
   give `C57BL6J-638850-raw_no_blank.h5ad`, which is what this repo holds at
   `data/abc_source/scratch/`. Verified: 500 genes, no name beginning `Blank`.

3. **Aggregated to region means** by `scripts/data/fetch_abc_reference.py`, at
   both `parcellation_substructure` and `parcellation_structure` level, unioned
   substructure-first, dropping any region with fewer than 50 cells. Result:
   **707 regions × 500 genes**, 6.7 MB, tracked in git.

4. **Scored** by `allen_alignment/qc/m4_expr_corr.py`: `log1p`, z-score each
   gene across regions, then Pearson across shared genes.

## RAW, not log2 — and why it matters

The Allen release ships both `C57BL6J-638850-raw.h5ad` and
`C57BL6J-638850-log2.h5ad`. **This repo uses the raw one.** Confirmed by value
distribution rather than by filename:

| file | min | max | mean | integers? |
| --- | --- | --- | --- | --- |
| `...-raw_no_blank.h5ad` (ours) | 1.0 | 548.0 | 5.44 | **yes** |
| `...-log2_no_blank.h5ad` | 0.42 | 9.68 | 2.01 | no |

Both files have identical shape, identical non-zero count (599,866,544) and
identical dtype, so they are **byte-for-byte the same size** and cannot be told
apart by `ls`. Only the values distinguish them.

This is worth knowing because the QC applies its own `log1p` before z-scoring.
Feeding it the log2 file would log already-logged data — every correlation in
step 6 would shift, and nothing in the output would say so. If this file is ever
re-fetched, check that the values are integers before trusting it.

## The derived tables: section x gene, one per plane

`data/reference/allen_abc_section_by_gene.csv` (53 sections x 500 genes, 0.5 MB,
tracked) comes from the SAME two source files, built by
`scripts/data/fetch_abc_section_reference.py`. It exists for the whole-section
correlation in step 6, which asks a tissue-level question -- does this section
look like brain at this AP -- and so needs a whole-slide profile rather than a
per-region one.

`allen_abc_sagittal_by_gene.csv` (50 bands x 500 genes) comes from the same
pass. The release was acquired coronally, but every cell carries CCF
coordinates, so it is a 3-D point cloud and can be cut the other way: a sagittal
band is every cell whose `z_ccf` falls in a 200 um slice of the ML axis. That
pooling is weighted by the AP thickness each coronal section stands in for --
they are 73-772 um apart, and without the correction 11.5% of the profile's mass
sits on the wrong AP.

Sanity check on the ML binning: bands mirrored about the midline correlate at
r = 0.994-0.997, as a nearly symmetric brain should. That also means a LEFT/RIGHT
flip of this axis would be almost undetectable from the data, which is why the
axis comes from the atlas frame rather than being inferred.

Axis identification, measured rather than assumed: `x_ccf` is AP (within-section
spread ~0.17 mm against 3.35 mm between sections, 20:1), `z_ccf` is ML (range
0-10.89 mm, median 5.71 against the atlas midline at 5.70), `y_ccf` is DV.

The coronal table carries `ap_mm`, the section's mean `x_ccf`, because the release's section
NUMBERS are its own acquisition index: they run backwards (higher = more
anterior) and are unevenly spaced (median 184 um, range 73-772 um). `x_ccf` is
the AP axis -- within a section it varies by ~0.17 mm against 3.35 mm between
section means, a 20:1 ratio, while y and z are the other way round. An atlas
index converts as `ap_mm = idx * ATLAS_RES_UM / 1000`, measured from the
anterior pole, the same frame the ABC coordinates use.

Coverage: 2.64M of the release's 4.33M cells carry a section label that joins to
the expression matrix, the same join `fetch_abc_reference.py` makes, so the two
tables describe the same cells.

## Re-fetching

Scripted end to end since 2026-10-19; `data/reference/README.md`, "Rebuilding them", has the
commands. `scripts/data/fetch_allen_sources.py` downloads both source files over
plain HTTPS from the public bucket (no `abc_atlas_access` client and no
credentials needed), drops the blank probes, and fetches the CCF ontology. The two
build scripts then write the tables, to `ALLEN_REFERENCE_OUT` if it is set.

The source files, verified 2026-10-19:

| file (under `s3://allen-brain-cell-atlas/`) | bytes | SHA-256 |
| --- | --- | --- |
| `expression_matrices/MERFISH-C57BL6J-638850/20230830/C57BL6J-638850-raw.h5ad` | 7,627,589,574 | `f412b0f53457af90b3c17ed786182624fb9b8c6922112a809653b7933f6de56f` |
| `metadata/MERFISH-C57BL6J-638850-CCF/20231215/views/cell_metadata_with_parcellation_annotation.csv` | 1,606,515,935 | `1e3ae23cc3f8d3d5839cc8a222798ff812fc66f4bd3c0fc8fca0c0279329874e` |

Rebuilt from those, all four files in `data/reference/` came out byte-identical
to the originals; their checksums are in `data/reference/README.md`. The
download is ~9.2 GB and gitignored.

## How this was established

The file was traced back on 2026-09-22 by comparing it against a copy staged
during earlier work: byte-for-byte identical over
all 7,464,193,638 bytes. The download call, dataset names and release versions
above come from the Allen cache manifest, not from that project.

**The canonical source is the Allen release named above.** Nothing here should
be re-derived from a working copy elsewhere: those can move or be cleaned up.

## Licence (reviewed 2026-10-19)

Both ABC Atlas pages this reference is built from state **"Data is being
share under the CC BY NC 4.0 license"**: `MERFISH-C57BL6J-638850` (the
expression) and `MERFISH-C57BL6J-638850-CCF` (the parcellation)
(<https://alleninstitute.github.io/abc_atlas_access/descriptions/MERFISH-C57BL6J-638850.html>).
The Allen Institute Terms of Use (updated 2022-05-05) say the same in their own
words: noncommercial use, derivatives allowed for noncommercial purposes, and
"You may not redistribute the Content or Improvements for commercial purposes
without our written permission."

So the derived tables **can** be redistributed, but only **with attribution**,
**noncommercially**, and **with the changes indicated**. They cannot be freely
redistributed. They are therefore excluded from the repository's Apache-2.0
license and attributed in `NOTICE` and `data/reference/README.md`, which also
cover `structure_tree_safe_2017.csv`, the Allen CCFv3 ontology (Allen Terms of
Use).

**Citation:** Yao, Z. et al. A high-resolution transcriptomic and spatial atlas
of cell types in the whole mouse brain. *Nature* 624, 317–332 (2023).
<https://doi.org/10.1038/s41586-023-06812-z> (the dataset pages link its bioRxiv
preprint, 10.1101/2023.03.06.531121).

**Still a decision, not a fact:** whether Vizgen's use of these tables, and of
this repository, counts as commercial. If it does, keeping them in a
distributed repository needs the Allen Institute's written permission, or the
tables need to move out of the repository (they are reproducible from the
scripts named above). That is for Vizgen to decide.

