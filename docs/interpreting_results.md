# Interpreting the results

What the numbers on the Results page (step 6) and in the downloads mean, and how
far each can be trusted. The QC thresholds are provisional: they are still being
calibrated against sections whose correct answers are known
([`data/positive_control/README.md`](../data/positive_control/README.md)).

## Alignment record

| item | meaning |
| --- | --- |
| **Cells labelled** | cells that landed inside an annotated Allen region. The rest fell outside the annotation, most often past the section edge, and carry no region. This is **coverage: completeness, not accuracy.** A section can be 98% labelled and still have many cells in the wrong region. |
| **vs automatic** | the same figure before curation. A drop means curated tissue moved off the annotated atlas, which is worth checking; a small rise is usual. |
| **Beyond atlas** | cells whose position lies past the end of the Allen volume. They count as unlabelled, and no alignment can label them: the reference stops there. Common on sagittal sections. |
| **Section** | for a coronal hemisection declared in step 2, which half of the brain the tissue is. |
| **Atlas slice** | the index on the plane's own axis: AP for coronal, ML for sagittal. It depends on the atlas resolution. |
| **Global transform** | the whole-section affine set in curation. Scale is a multiplier: 0.967 means the atlas was shrunk 3.3%. |
| **Local warp** | the CSRBF grid. **min \|J\|**, the smallest Jacobian determinant, must stay well above zero, or the warp would fold tissue onto itself. |

## Whole-section correlation (r)

One point per gene shared with the Allen panel: its mean counts per cell in the
Allen reference section at the chosen level, against this section, both
log-scaled. **r** is the log10 Pearson correlation over those genes.

- It is a **tissue-level** check. It uses no region labels and no alignment, only
  the panel counts, so it answers "does this section look like brain at this
  level?"
- **Curation cannot change it.** A high r confirms the level choice and the
  tissue; it says nothing about whether the regions are drawn in the right
  places.
- The reference is a real Allen section at the matching level (coronal) or a
  200 µm band (sagittal), not a whole-brain average.

## Region specificity

For each region with at least 50 cells: does its expression match its *own*
Allen reference profile better than any *other* region's? Genes are log-scaled
and z-scored across regions, so it compares patterns, not abundance.

- **Verdict:** PASS when the median self-correlation is at least 0.30, otherwise
  WARN. One verdict for the whole section.
- **Region-by-region table:** *self r* (match to its own profile), *best match*
  and *best r* (the region it matches most), and *gap* (best r minus self r). A
  highlighted best match means the region resembles another one more than
  itself.
- **Reading a mismatch:** a neighbour, parent or the same cortical layer is a
  soft boundary; a fibre tract scores low because it has little region-specific
  signal. A distant match, or a large gap on a well-populated region, is worth
  checking in the overlay.
- It does not use the level index, so it is an **independent** check on that
  choice, but it only supports the registration. Judge it together with the
  overlay.

## Per-region agreement

One row per region: does the region resemble its own reference at all? Pearson
and Spearman on raw log counts, the region against itself only, with no
comparison against neighbours.

- Each row's PASS/WARN is driven by the specificity score above; the absolute
  correlations decide a row only when specificity cannot be computed.
- Use it to find the few bad regions inside an otherwise sound fit. The two
  measures often disagree: a region can match its own profile well and still
  resemble a neighbour more.

## Spatial hex-bin QC

Cells are grouped into hexagons (100 µm by default), and each bin's expression
is compared with the Allen profile of its mapped region. It is an exploratory
map of where agreement is weaker, **not** a PASS/FAIL test. Bins with fewer
than 10 cells are not scored. Smaller bins lower the median correlation through noise,
not through a worse registration, so compare runs only at the same bin size.

## The per-cell table

`cell_metadata_with_allen_regions_<section>[_curated].csv` is the input
`cell_metadata.csv` with these columns added:

| column | meaning |
| --- | --- |
| `allen_region_id` | Allen structure id (0 = unlabelled) |
| `allen_acronym`, `allen_name` | the leaf region the cell sits in |
| `allen_parent` | that region's parent |
| `allen_ontology_path` | the full hierarchy, root first, `>`-separated |
| `mapping_status` | `mapped`, `unmapped`, or `id_not_in_tree` |
| `atlas_ap_um`, `atlas_si_um`, `atlas_lr_um` | the cell's position in the atlas, µm |

The `_curated` file reflects your curation; the file without it is the automatic
fit. Labels are always leaf-level, even when the curation overlay showed a
coarser hierarchy for readability.
