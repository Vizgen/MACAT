# Architecture

How the tool is put together. For installing and using it, see the
[README](../README.md). For where each module lives, see
[`repository_layout.md`](repository_layout.md). For what changed in each
version, see [`CHANGELOG.md`](../CHANGELOG.md).

## Components

```text
browser ──HTTP──> FastAPI server (web/server.py)
                    ├── serves the prebuilt page   web/frontend/dist/
                    ├── job state machine          web/jobs.py
                    ├── registration worker        web/_register_worker.py  (subprocess)
                    └── finalize worker            web/_finalize_worker.py  (subprocess)
                          └── pipeline core        allen_alignment/*
```

- **The server** answers the API and hands the browser the web page. A job moves
  through fixed states (`INPUT_READY` → `ORIENTATION_CONFIRMED` → `AP_CONFIRMED`
  → `AUTO_REGISTRATION_DONE` → `MANUAL_CURATION_CONFIRMED` → `FINALIZING` →
  `COMPLETED`, or `FAILED`), persisted in `jobs/<id>/job.json`.
- **Workers** run registration and finalisation in their own processes, so a
  long job survives the request that started it and cannot take the server down.
- **The pipeline core** (`allen_alignment/`) holds the maths: density raster,
  registration, warp, label sampling, figures and QC. `pipeline_api.py` is a thin
  facade over it.

## Toolchains

There are two, and they are independent.

| | what | who needs it |
| --- | --- | --- |
| Python 3.11 | the server, workers and pipeline (`requirements-web.txt`, or the `macat` conda env from `environment.yml`) | everyone |
| Node 20 + npm | builds the web page from `web/frontend/src` (React) into `web/frontend/dist` (Vite) | developers changing the frontend only |

- **The conda env is Python only.** It contains no `node` and no `npm`, so it
  cannot build the frontend; activating it is not enough to run `npm run build`.
- **The built page is committed** (`web/frontend/dist/`, ~300 KB), so users
  never need Node. Builds are deterministic, and `tests/test_frontend_dist.mjs`
  fails if the committed page differs from what the source builds, so a frontend
  change cannot ship with a stale page. `.gitattributes` keeps `dist/` byte-exact
  on Windows checkouts and out of diffs.

## Registration

- **Automatic step:** a cell-density image of the section is matched to the
  chosen Allen slice by silhouette. The result is a forward affine (position and
  scale), and each cell takes the label of its nearest atlas voxel. It is numpy
  only (`allen_alignment/raster.py`): no torch, no GPU, about 2 s per section.
- **Curation:** a global affine plus an optional local CSRBF warp grid, both
  computed identically in the browser (`warp.js`) and the backend (`warp.py`)
  and checked against each other by `validation/_grid_parity.py`.
- **Hemisections** are declared by the user in step 2. The fit then uses only
  the declared half of the atlas slice.

## Data sources

- **Local folder:** a MERFISH region folder with `cell_metadata.csv` and
  `cell_by_gene.csv`. Optional: `images/mosaic_DAPI_z3.tif` with
  `images/micron_to_mosaic_pixel_transform.csv` (the DAPI background), and
  `detected_transcripts.parquet` or `.csv` (the transcript background).
- **The data folder:** one local folder or `s3://` prefix holding experiments
  as MERSCOPE writes them, `<experiment>/region_*/` (`web/datafolder.py`).
  Step 1 lists every region folder exactly that deep that has both cell tables.
  The choice is saved in `<output root>/settings.json`; `MACAT_DATA_DIR`
  overrides it. It is scanned only when set or on Rescan, so slow or network
  drives do not slow step 1; an `s3://` prefix is one listing call. Listing
  folders shows the file system, so the server answers these requests only from
  the machine it runs on, which includes an SSH tunnel.
- **Answer keys:** a region folder may carry `expected.json`, its known plane,
  half, orientation and atlas level (`web/answer_key.py`; format in
  `docs/allen_annotated_data.md`). MACAT reads it from the data itself, local
  or S3 (it is fetched with the cell tables): the level is shown in "Your
  sections" and step 3, and step 2 is pre-filled from it. Nothing about known
  answers is kept inside MACAT.
- **S3:** an `s3://` path to the same folder layout. The server fetches the
  files the app reads into a local cache (`data/s3_cache/`, or
  `ALLEN_S3_CACHE`) and builds the job on that folder. **After step 1 an S3 job
  behaves exactly like a local one;** only creation differs. The job records the
  S3 URL and each file's size and ETag. Archived objects (Glacier, Deep Archive)
  are reported, never requested. Access is through the AWS CLI and the user's
  own credentials.

## Storage layout

```text
<output root>/                         pipeline_output/, or ALLEN_OUT_ROOT
└── jobs/<job id>/
    ├── job.json                       state, choices, result paths
    ├── notes.json                     the Resume list's notes
    ├── register.log, register.status  worker progress
    └── outputs/                       everything the job produced
```

- **Each job writes only into its own `outputs/` folder,** so two jobs on the
  same section never overwrite each other. File names inside still carry the
  section tag, which keeps them readable.
- Jobs created before per-job folders keep the flat paths in the output root
  that they recorded.
- The Allen atlas volumes come from BrainGlobe, downloaded on first use to
  `~/.brainglobe` (about 600 MB at 25 µm, 220 MB at 50 µm). The atlas resolution
  and the plane grid must match (`ALLEN_ATLAS_RES_UM`, `ALLEN_PLANE_DX_UM`;
  25 µm by default).

## Licensing of data

- **Code:** Apache-2.0 ([`LICENSE.md`](../LICENSE.md)). No GPL dependency.
- **Allen data:** the four Allen-derived files MACAT reads from `data/reference/`
  and the atlas volumes BrainGlobe downloads are under CC BY-NC 4.0 and the
  Allen Institute Terms of Use: **noncommercial use only**, with attribution.
  They are not Apache-2.0 and **are not shipped**: each user builds the
  reference files from Allen's public release
  ([`data/reference/README.md`](../data/reference/README.md)), and BrainGlobe
  downloads the atlas on first use.
- Attribution and citations: [`NOTICE`](../NOTICE).
