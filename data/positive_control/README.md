# Allen annotated data: MERFISH sections with known-correct answers

Twelve sections from published whole-brain MERFISH datasets whose authors
registered every cell to the Allen CCF. Each is packaged as an ordinary
experiment, so it runs through MACAT exactly as a user's section would. Every
answer the app gives can then be scored against the known one:

- **step 2 orientation:** the angle and flip that put the section dorsal-up;
- **step 3 level:** the coronal (AP) or sagittal (ML) slice index, at 25 and
  50 µm;
- **registration and curation:** each cell's region.

Once built, set MACAT's data folder (step 1) to `sections/`: every section
appears under "Your sections" with its known level, step 2 is pre-filled and
step 3 opens on the level, all read from each section's `expected.json`.

## The three sets

| set | source | sections | why it is here |
| --- | --- | --- | --- |
| **coronal, whole** | Allen ABC MERFISH `MERFISH-C57BL6J-638850` (Yao et al. 2023) | 5 | whole bilateral sections: the normal case. This is the dataset the QC reference is built from, so `reference_holdout/` rebuilds that reference **without** these five, for scoring the QC without circularity |
| **coronal, hemisection** | Zhuang-lab `Zhuang-ABCA-1` (Zhang et al. 2023) | 5 | the published metadata holds **one hemisphere** per coronal section (4–5 mm wide; CCF ML up to the midline). This set tests the step-2 **hemisection** setting: each is declared as its half (`hemisection` in `sections.csv`, all `left`) |
| **sagittal, whole** | Zhuang-lab `Zhuang-ABCA-3` (Zhang et al. 2023) | 2 | real sagittal sections: one near the midline, one lateral |

The Zhuang sets are independent of the QC reference: a different lab, different
animals, and a different panel of 1,122 genes.

## The answers each section carries

The build writes each section's known answers to its `expected.json`, which
MACAT reads (see [`docs/allen_annotated_data.md`](../../docs/allen_annotated_data.md)),
and all of them together to `sections.csv`, the machine-readable answer key.
Neither is included in this repository: both are derived from the published
datasets, so they come with the build. Once built, step 1 shows each section's
level and step 2 is pre-filled from it.

- **Level:** the cells' mean CCF position along the slice axis, divided by the
  resolution, measured from the atlas corner (the app's index convention). AP
  is measured from the anterior pole, ML from the atlas edge.
- **±:** the section's own spread along that axis (1 SD). Allen's sections are
  oblique (about ±0.2 mm), Zhuang's much less so.
- **Mirror:** a sagittal index reflected across the midline cuts the same
  anatomy, so either answer is correct.
- **Orientation:** none of the twelve needs a flip. The angle is found by
  searching every angle and flip step 2 offers, through `config.orient_rotflip`,
  for the best match of image *y* to DV and image *x* to the in-plane atlas
  axis (ML for coronal, AP for sagittal, posterior to the right). Every fit
  scored ≥ 1.993 of 2. For coronal sections, left-right handedness is not
  established, and region labels do not depend on it.

## Contents

| path | tracked? | what |
| --- | --- | --- |
| `sections.csv` | no | the answer key, machine-readable, one row per section |
| `README.md` | yes | this file |
| `sections/<name>/region_R1/cell_metadata.csv` | no | `EntityID`, `center_x`, `center_y` (µm, the section's own frame) |
| `sections/<name>/region_R1/cell_by_gene.csv` | no | `EntityID` × genes, raw counts (500 for Allen, 1,122 for Zhuang) |
| `sections/<name>/region_R1/ground_truth.csv` | no | registered cells: CCF `x_ccf`/`y_ccf`/`z_ccf` (mm), `division`, `structure`, `substructure` |
| `sections/<name>/region_R1/expected.json` | no | that section's answers, which MACAT reads |
| `reference_holdout/` | no | the three ABC tables without the five Allen sections |

Everything but this README is built locally (about 1.3 GB) and gitignored: the
Allen part is CC BY-NC 4.0, and the Zhuang part is CC BY 4.0 with attribution
(see the root `NOTICE`).

## Building it

From the repo root, in the `allen_reference` environment, after
`scripts/data/fetch_allen_sources.py` ([`data/reference/README.md`](../reference/README.md), "Rebuilding them"):

```bash
python scripts/data/build_positive_control.py    # downloads the Zhuang data (~2.6 GB) if missing; ~5 min
```

Sections are chosen by fixed rules in the script, so a rebuild picks the same
ones.
