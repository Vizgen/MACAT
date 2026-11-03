# Web application

The production web workflow is a FastAPI backend plus a React/Vite frontend.
Each job persists its experiment, orientation, selected AP slice, registration
bundle, curation transform, final outputs, and QC artifacts.

## Backend

`web/server.py` exposes the job state machine and co-registered display assets.
Heavy registration and finalization run in isolated worker processes through
`web/_register_worker.py` and `web/_finalize_worker.py`.

The manual curation canvas supports:

- global affine translation, rotation, and anchored scaling;
- selectable Allen hierarchy overlays extracted as closed polygon rings;
- DAPI, cell-density, and all-gene transcript-density backgrounds;
- validated local correction using a CSRBF warp grid;
- leaf-level cell and transcript annotation on finalization.

## Development

From the repository root, start the backend:

```bash
uvicorn web.server:app --host 0.0.0.0 --port 8700
```

In another shell, start the frontend:

```bash
source ~/.nvm/nvm.sh
nvm use 20
cd web/frontend
npm ci
npm run dev
```

## Single-server build

```bash
source ~/.nvm/nvm.sh
nvm use 20
cd web/frontend
npm ci
npm run build
cd ../..
uvicorn web.server:app --host 0.0.0.0 --port 8700
```

The backend serves `web/frontend/dist` when the production build exists.
Generated frontend dependencies and build artifacts are ignored by Git.

## Validation

```bash
python -m unittest discover -s tests -p 'test_*.py'
node tests/test_affine_ui.mjs
node tests/test_warp_ui.mjs
cd web/frontend && npm run build
```

Run Python validation in the `macat` environment.
Frontend validation requires Node.js 20.19 or newer (Vite 8).
