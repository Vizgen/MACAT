# Changelog

What changed in each version of MACAT, for people using it. The version is
stamped into every `report.zip` (`manifest.json`), so a report can be traced to
the code that made it.

## 0.7.0 — 2026-11-03

Step 5's local correction is simpler:

- **Two tools:** Direct drag and Advanced grid. Elastic push and Landmark pairs
  are no longer offered; warps saved with them still open.
- **New order:** Warp layers, then Grid density, then the tools.
- **Warp layers has a (?)** explaining when to add a layer: when a move is
  refused for going past the limit, add a layer and carry on.
- **Grid density** offers 13×13, 9×9 and 5×5 (4×4 and 7×7 removed), and warns
  that switching discards the live grid's correction unless you add a layer
  first.

## 0.6.0 — 2026-11-03

Fixes from the first test on macOS (Apple Silicon), which otherwise passed
every step:

- **Confirm alignment works on an unchanged automatic fit.** Step 5 no longer
  requires an edit before the first confirmation; accepting the automatic fit
  as it is is a valid curation. A job already confirmed still needs an edit to
  confirm again.
- **Quoted paths work.** A path pasted with quotes around it (Windows' "Copy as
  path") is accepted in both the data folder and the experiment folder.
- **Reports say which atlas was used.** `manifest.json` in `report.zip` records
  the atlas again (it was always empty); older jobs get it from their
  registration file.
- **Tested on macOS** (Apple Silicon), as well as Linux and Windows.

## 0.5.1 — 2026-11-02

- **The Allen reference files are no longer included.** Their licence (CC BY-NC
  4.0) allows noncommercial use only, so each user builds them once from Allen's
  public release; see the README, "Build the Allen reference files". Until
  they are built, MACAT warns at startup, and registration stops with a
  message naming the missing file and the build step.
- The Allen build also works without conda: `requirements-allen-reference.txt`
  for uv or pip, with the same pins.
- `validation/_affine_parity.py` runs again, and skips its overlay checks when
  there is no registration to compare.
- The README lists [uv](https://docs.astral.sh/uv/) as an install option. Tested
  on Linux and Windows.

## 0.5.0 — 2026-10-28

- **One way in:** step 1 lists sections from your data folder only.
- **Known answers travel with the data:** a region folder can carry an
  `expected.json`. MACAT then shows the correct level beside the section,
  pre-fills step 2 and opens step 3 on that level, for local and S3 folders.
- The Allen annotated data builds as `<name>/region_R1/`, like any experiment.

## 0.4.16 — 2026-10-28

- Example data for new users: the Vizgen mouse-brain showcase, and
  `docs/allen_annotated_data.md` with download links and build commands.
- The conda environment is named `macat`, from `environment.yml`.

## 0.4.14 — 2026-10-27

- Each job writes into its own folder, so two jobs on one section never
  overwrite each other.
- The tool is named MACAT. The README is rewritten for new users, with new
  architecture, troubleshooting and results guides.
- The built web page is included, so users never need Node.js.
- **Data folder:** set one local folder or `s3://` prefix in step 1, and every
  `<experiment>/region_*` in it is listed.

## 0.4.13 — 2026-10-27

- The optional LDDMM registration (STalign, GPL-3.0) is removed. No torch and no
  GPU anywhere; old `.pt` fits are read from their `.npz` conversions.

## 0.4.12 — 2026-10-27

- Python 3.11 and Pillow 13, with a trimmed environment file (no known
  vulnerable packages).

## 0.4.11 — 2026-10-27

- Registration runs on numpy and scipy only, giving the same results as before.
  Fit files are `.npz`.

## 0.4.10 — 2026-10-23

- Sections with known answers show their level in step 1, and step 3 starts on
  it.
- A Notes column in the Resume list.
- Results draws cell density where there is no DAPI image.

## 0.4.9 — 2026-10-21

- Hemisection outlines close along the section's edge rather than with a
  straight chord.
- The step 5 region overlay uses the rank palette, and the cell-density
  background is grayscale.

## 0.4.8 — 2026-10-20

- S3 network failures are retried once and reported as such.
- Step 5 shows grid density and warp layers only in Local correction.

## 0.4.7 — 2026-10-20

- Experiments can be read from S3 (`s3://` paths, cached locally; archived
  objects are reported, never restored).
- Coronal hemisections can be declared in step 2.

## 0.4.6 — 2026-10-20

- Allen annotated data: 12 published sections with known answers (whole
  coronal, coronal hemisections and sagittal), for checking MACAT's results.

## 0.4.5 — 2026-10-19

- Apache-2.0 licence, NOTICE, Code of Conduct and contribution guide. The Allen
  data's terms reviewed and attributed, with a documented rebuild route.

## 0.4.4 — 2026-10-19

- Resume list: select jobs, download completed reports as one .zip, and delete
  jobs.

## 0.4.3 — 2026-10-16

- Export of the per-cell Allen labels for the MERSCOPE Vizualizer, in Results
  and in `report.zip`.

## 0.4.2 — 2026-10-07

- Cells beyond the edge of the Allen atlas are counted and shown in steps 4–6.

## 0.4.1 — 2026-10-07

- Security updates to dependencies. Registration results unchanged.

## 0.4.0 — 2026-10-07

- 25 µm is the default atlas resolution. Jobs registered at another resolution
  are refused rather than reinterpreted, and keep their level by position.

## 0.2.0 — 2026-09-22

- Sagittal sections, the rebuilt step 6 figure, interactive panels and report
  archive fixes.

## 0.1.0 — 2026-08-31

- First tagged version: the six-step web app (input, orientation, level,
  registration, curation, results).

Versions 0.3.0 and 0.4.15 were not released as such.
