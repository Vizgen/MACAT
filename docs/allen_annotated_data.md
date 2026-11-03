# Allen annotated data: sections with known answers

Twelve MERFISH mouse-brain sections whose cells were already registered to the
Allen CCF by the authors of the data. MACAT can run each one like any other
experiment, so you can:

- try MACAT without your own data;
- check your answers: each section comes with its correct orientation, atlas
  level and every cell's region.

| set | sections | source |
| --- | --- | --- |
| coronal, whole | 5 | Allen Brain Cell Atlas, `MERFISH-C57BL6J-638850` (Yao et al. 2023) |
| coronal, hemisection (left half) | 5 | Zhuang lab, `Zhuang-ABCA-1` (Zhang et al. 2023) |
| sagittal, whole | 2 | Zhuang lab, `Zhuang-ABCA-3` (Zhang et al. 2023) |

The sections are not downloaded ready-made: a script cuts them out of the public
Allen Brain Cell Atlas release, the same way every time. (The scripts and folders
below use the name `positive_control`.)

## Licences

The data is not part of MACAT's Apache-2.0 licence.

- `MERFISH-C57BL6J-638850`: **CC BY-NC 4.0** and the Allen Institute Terms of
  Use: noncommercial use only, with attribution.
- `Zhuang-ABCA-1`, `Zhuang-ABCA-3`: **CC BY 4.0**, with attribution.

Citations are in [`NOTICE`](../NOTICE).

## What you need

| | |
| --- | --- |
| download | about **12.5 GB**, from Allen's public bucket; no account needed |
| disk | about **22 GB** while building; about **1.3 GB** for the 12 sections afterwards |
| memory | about **22 GB of RAM** at peak |
| time | about 15 minutes on a fast connection |
| software | a clone of this repository and conda |

## Build them (recommended)

From the repository root:

```bash
# a separate environment, for building only
conda env create -f environment_allen_reference.yml
conda activate allen_reference

# 1. download the Allen source files and prepare them (~9.2 GB)
python scripts/data/fetch_allen_sources.py

# 2. download the Zhuang files (~3.2 GB) and build the 12 sections
python scripts/data/build_positive_control.py
```

Both steps skip files that are already there and resume an interrupted download,
so re-running is safe.

The sections are written to `data/positive_control/sections/<name>/region_R1/`
(under `ALLEN_POSITIVE_CONTROL_DIR` if set), the same `<experiment>/region_*/`
layout MERSCOPE writes. Each region folder holds:

- `cell_metadata.csv` and `cell_by_gene.csv`: what MACAT reads;
- `expected.json`: that section's correct orientation and level, its answer
  key (see below);
- `ground_truth.csv`: every cell's published CCF region.

When the build is done you can delete the downloads in `data/abc_source/`
(about 12 GB).

## Or download the source files directly

The build scripts download these for you. To fetch them yourself, for example
on a faster machine, use these links; the
[Allen Brain Cell Atlas documentation](https://alleninstitute.github.io/abc_atlas_access/intro.html)
describes each dataset:

| file | size |
| --- | --- |
| [`C57BL6J-638850-raw.h5ad`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/expression_matrices/MERFISH-C57BL6J-638850/20230830/C57BL6J-638850-raw.h5ad) | 7.63 GB |
| [`cell_metadata_with_parcellation_annotation.csv`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/metadata/MERFISH-C57BL6J-638850-CCF/20231215/views/cell_metadata_with_parcellation_annotation.csv) (638850) | 1.61 GB |
| [`Zhuang-ABCA-1-raw.h5ad`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/expression_matrices/Zhuang-ABCA-1/20230830/Zhuang-ABCA-1-raw.h5ad) | 1.19 GB |
| [`cell_metadata.csv`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/metadata/Zhuang-ABCA-1/20241115/cell_metadata.csv) (ABCA-1) | 0.66 GB |
| [`ccf_coordinates.csv`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/metadata/Zhuang-ABCA-1-CCF/20230830/ccf_coordinates.csv) (ABCA-1) | 0.22 GB |
| [`Zhuang-ABCA-3-raw.h5ad`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/expression_matrices/Zhuang-ABCA-3/20230830/Zhuang-ABCA-3-raw.h5ad) | 0.66 GB |
| [`cell_metadata.csv`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/metadata/Zhuang-ABCA-3/20241115/cell_metadata.csv) (ABCA-3) | 0.37 GB |
| [`ccf_coordinates.csv`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/metadata/Zhuang-ABCA-3-CCF/20230830/ccf_coordinates.csv) (ABCA-3) | 0.13 GB |
| [`parcellation_to_parcellation_term_membership_acronym.csv`](https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/metadata/Allen-CCF-2020/20230630/views/parcellation_to_parcellation_term_membership_acronym.csv) | < 0.01 GB |

To put them where the build scripts look, run this from the repository root. It
needs only `curl` and keeps the bucket's folder layout:

```bash
BUCKET=https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com
DEST=data/abc_source/abc_atlas
for key in \
  expression_matrices/MERFISH-C57BL6J-638850/20230830/C57BL6J-638850-raw.h5ad \
  metadata/MERFISH-C57BL6J-638850-CCF/20231215/views/cell_metadata_with_parcellation_annotation.csv \
  expression_matrices/Zhuang-ABCA-1/20230830/Zhuang-ABCA-1-raw.h5ad \
  metadata/Zhuang-ABCA-1/20241115/cell_metadata.csv \
  metadata/Zhuang-ABCA-1-CCF/20230830/ccf_coordinates.csv \
  expression_matrices/Zhuang-ABCA-3/20230830/Zhuang-ABCA-3-raw.h5ad \
  metadata/Zhuang-ABCA-3/20241115/cell_metadata.csv \
  metadata/Zhuang-ABCA-3-CCF/20230830/ccf_coordinates.csv \
  metadata/Allen-CCF-2020/20230630/views/parcellation_to_parcellation_term_membership_acronym.csv
do
  mkdir -p "$DEST/$(dirname "$key")"
  curl -L -C - -o "$DEST/$key" "$BUCKET/$key"     # -C - resumes a partial file
done
```

With the AWS CLI, `aws s3 cp --no-sign-request s3://allen-brain-cell-atlas/<key> <DEST>/<key>`
does the same, also without an account.

Then run the two build commands above. They find the files and skip downloading
them; step 1 still prepares the 638850 matrix, which the build needs.

## Use them

In step 1, set the **Data folder** to the `sections` folder, for example
`/path/to/MACAT/data/positive_control/sections`. All twelve appear under **Your
sections**, each with its correct level beside it, for example
`region_R1 · level 176 ±7`.

Because each section carries its answer key, MACAT also:

- pre-fills step 2 with the section's plane, half (for the hemisections),
  rotation and flip, for you to check and confirm;
- opens step 3 on the correct level, with a note giving the level and its
  tolerance.

To check MACAT's labels against the published ones, compare the per-cell table
in a job's download (`per_cell/`) with the section's `ground_truth.csv`, which
holds each cell's published Allen region; both are keyed by `EntityID`.

How the sections and their answers are built:
[`data/positive_control/README.md`](../data/positive_control/README.md).

## Answer keys: `expected.json`

Any region folder can carry its known answers, not only the Allen annotated
data. Put an `expected.json` next to `cell_metadata.csv`, and MACAT shows and
pre-fills those answers wherever the folder lives: on your computer or on S3.
MACAT reads these fields; all are optional, and anything else in the file is
ignored:

| field | meaning |
| --- | --- |
| `plane` | `"coronal"` or `"sagittal"` |
| `hemisection` | `"left"` or `"right"` for half a coronal section; `""` for a whole one |
| `orientation_angle` | step 2's rotation, in degrees |
| `orientation_flip` | `"none"`, `"x"` (vertical) or `"y"` (horizontal) |
| `idx_25um`, `idx_25um_tolerance` | the correct atlas level at 25 µm, and its ± tolerance in levels |
| `idx_25um_mirror` | sagittal only: the level mirrored across the midline, equally correct |
| `idx_50um`, `idx_50um_tolerance`, `idx_50um_mirror` | the same at 50 µm |

The level shown is the one for the atlas resolution MACAT runs at. For example:

```json
{
  "plane": "coronal",
  "hemisection": "left",
  "orientation_angle": 0.0,
  "orientation_flip": "none",
  "idx_25um": 285,
  "idx_25um_tolerance": 4
}
```
