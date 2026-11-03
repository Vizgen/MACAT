# Repository structure

The repository separates production runtime code from research commands,
versioned data, documentation, and generated outputs.

```text
.
|-- allen_alignment/        # the pipeline core, one module per concern
|   |-- plane.py            # pixel <-> micron conversions (no dependencies)
|   |-- atlas.py            # CCF volume, ontology, hierarchy roll-up
|   |-- warp.py             # CSRBF kernel + lattice + validation (MIRRORED in JS)
|   |-- transforms.py       # affine / composite applied to the label raster
|   |-- boundaries.py       # overlay contours, labels, fills (read-only)
|   |-- figures.py          # matplotlib rendering
|   |-- registration.py     # density -> fitted plane (numpy; core in raster.py)
|   |-- raster.py           # density raster, min-max, nearest-voxel label lookup
|   |-- finalize.py         # labels + deliverables
|   `-- qc/                 # QC modules imported by the app
|-- web/                    # FastAPI + React/Vite application
|   `-- frontend/dist/      # the BUILT page, committed; rebuild with src/ changes
|   `-- frontend/src/curation/tools/   # correction tools, one per file;
|                           #   <id>.js is behaviour (node-testable),
|                           #   <id>.panel.jsx the sidebar controls,
|                           #   index.js the ONLY registration point
|-- scripts/
|   |-- data/               # supported reference-data tools
|   `-- registration/       # runtime logging helper for QC commands
|-- data/
|   |-- reference/          # Allen reference tables (you build them; GITIGNORED)
|   |-- abc_source/         # ABC atlas download, ~8.5 GB, GITIGNORED
|   |-- positive_control/   # Allen annotated sections (you build them)
|   `-- metadata/           # optional host-local maps (GITIGNORED)
|-- docs/                   # architecture, troubleshooting, results guide
|-- tests/                  # automated tests
|-- validation/             # browser-vs-backend parity checks (warp grid, affine)
|-- config.py               # shared configuration
`-- pipeline_api.py         # thin facade re-exporting allen_alignment/*
```

## Rules

1. Run Python commands from the repository root using `python -m ...`.
1b. `pipeline_api.py` is a FACADE that re-exports `allen_alignment/*`. It exists
   so existing callers keep working; new code should import the specific module
   (`from allen_alignment.warp import validate_warp_grid`), which states the real
   dependency and avoids pulling torch in to read a constant. The module graph is
   acyclic: plane, atlas -> warp -> transforms, boundaries -> figures ->
   finalize, registration. Keep it that way; a cycle means a concern is in the
   wrong file.
1d. A correction tool is a module: add one by writing `<id>.js` + `<id>.panel.jsx`
   under `web/frontend/src/curation/tools/` and listing it in that directory's
   `index.js`; remove one by deleting the same three things. `CurationStep.jsx`
   names no tool. A tool may only use the `ed` editor context it is handed, and
   must end every gesture by writing the shared control lattice -- which is why a
   new tool needs no backend change at all.
1c. A test that monkey-patches a helper must patch the module that USES it, not
   the facade -- `from .atlas import X` binds a second name, so patching the
   facade silently does nothing. See tests/test_depth2_polygons.py.
2. Application-imported code belongs in `allen_alignment/` or `web/`. Nothing
   new should be added to `pipeline_api.py` itself -- it is only a facade.
3. Standalone commands belong under the matching `scripts/` category.
4. Versioned reference inputs belong under `data/`; generated outputs do not.
   Two `data/` subtrees are deliberately gitignored: `abc_source/` (bulk
   upstream download) and the host-local `metadata/*.local.json` /
   `metadata/excluded_regions.txt` region maps (acquisition paths).
5. Superseded scripts, notebooks, and reports are recovered from Git history
   rather than retained in the active product tree.
6. Every user-visible change gets a line in `CHANGELOG.md`.
