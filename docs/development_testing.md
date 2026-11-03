# Testing a half-finished change

How to exercise this tool while it is mid-development: what to run after each
kind of edit, how to drive the UI without a full pipeline run, and how to stop
the servers you started without disturbing anyone else on the box.

For a server that will not start at all — port already bound, wrong environment,
stale frontend — see [`troubleshooting.md`](troubleshooting.md).
That section covers a broken start; this one covers day-to-day iteration.

## 0. Before any of this works

**Host-specific folders come from the environment, never from the code.**
`ALLEN_TRAIN_BASE=<folder of experiments>` gives the command-line QC scripts
(`config.REGIONS`) sections by key. The app does not use it: step 1 lists
sections from its data folder.

**The Allen reference files must be built** ([README](../README.md#build-the-allen-reference-files-required-once)).
Without them, the tests that need them skip.

**Two toolchains, and the conda environment is not one of them for the
frontend.** `macat` contains no `node` and no `npm`, and Vite 8 needs Node
20.19 or newer. Every `npm` command below
assumes Node 20 is active:

```bash
conda activate macat          # backend: BrainGlobe, FastAPI, numpy/scipy
source ~/.nvm/nvm.sh && nvm use 20      # frontend: check with `node --version`
```

`node_modules/` is gitignored (~41 MB), so on a fresh clone — or after
`package-lock.json` changes — install first or every `npm run build` and
`npm run dev` will fail:

```bash
cd web/frontend && npm ci && cd ../..
```

Full first-time setup, including creating the conda environment from
`environment.yml`, is in the [README](../README.md#install); the
two toolchains are described in [`architecture.md`](architecture.md#toolchains).

### `npm ci` reports vulnerabilities. That is expected.

`npm audit` reports **0 vulnerabilities** since Vite 8 (v0.4.0, 2026-10-07).
If that changes, remember that every finding so far came through `vite` /
`@vitejs/plugin-react`, which are **devDependencies**. What ships is
`web/frontend/dist/`, which is static JS, CSS and an icon served by FastAPI. No
Node runtime and none of these packages exist in production.

The dev server still binds to `127.0.0.1` rather than `0.0.0.0`. It proxies
`/api` straight to the backend, so exposing it on a shared machine would expose
the backend with it, and a dev server is not a hardened server whatever the
audit says. (The esbuild advisory GHSA-67mh-4wv8-2f99, which made this urgent,
was fixed by the Vite 8 upgrade.) If you really need to reach the dev server
from another machine, prefer an SSH tunnel; the escape hatch is
`VITE_DEV_HOST=0.0.0.0 npm run dev`, and you should know why you are using it.

---

## 1. What to run after what

Nothing here needs a GPU, a registration run, or the web server. Times are
measured on a Linux server.

| You changed | Run | Time |
| --- | --- | --- |
| `web/frontend/src/warp.js` or `elastic.js` | `node tests/test_warp_ui.mjs`, `node tests/test_elastic_ui.mjs`, **and** `python validation/_grid_parity.py` | <1 s + ~1 min |
| a correction tool under `curation/tools/` | `node tests/test_curation_tools.mjs` | 0.3 s |
| `allen_alignment/warp.py` or `transforms.py` | `python -m unittest discover -s tests -p 'test_*.py'` **and** the parity harness | ~30 s |
| `allen_alignment/boundaries.py` or `atlas.py` | the Python suite | ~30 s |
| anything under `web/frontend/src/` | `npm run build` (or use the dev server, §3), then commit `web/frontend/dist/` with the change; `node tests/test_frontend_dist.mjs` checks it | ~1 s + ~2 s |
| `config.py`, `web/jobs.py` | the Python suite | ~30 s |

Everything, before you commit:

```bash
conda activate macat
source ~/.nvm/nvm.sh && nvm use 20
python -m unittest discover -s tests -p 'test_*.py'
node tests/test_affine_ui.mjs
node tests/test_warp_ui.mjs
node tests/test_elastic_ui.mjs
node tests/test_curation_tools.mjs
python validation/_grid_parity.py
cd web/frontend && npm ci && npm run build && cd ../..
```

### The one that is easy to skip and shouldn't be

`validation/_grid_parity.py` is the only thing checking that
`allen_alignment/warp.py` and `web/frontend/src/warp.js` still agree. They are
deliberate line-by-line mirrors: change a constant or a validation rule in one
and the other must change in the same commit, or the browser will accept a warp
the backend later rejects — after the user has confirmed it. The unit tests will
not catch that. Neither will the build.

### Running one test instead of all of them

There is no pytest in this environment, and **`python -m unittest
tests.test_warp_grid...` does not work** — `tests/` has no `__init__.py`, so it is
not an importable package and you get
`ModuleNotFoundError: No module named 'tests.test_warp_grid'`. Two forms that do
work, both from the repository root:

```bash
# one file
python -m unittest discover -s tests -p 'test_warp_grid.py'

# one class or one method — each test file has its own __main__ block
python tests/test_warp_grid.py TestValidateWarpGrid -v
python tests/test_warp_grid.py TestValidateWarpGrid.test_identity_accepted -v
```

The second form is the fast one: 11 tests in ~5 s against ~30 s for the whole
suite.

---

## 2. Testing logic without the browser

Most of the interesting code is deliberately importable by plain `node`, with no
React and no DOM: `warp.js`, `elastic.js`, `affine.js`, and every correction
tool's `<id>.js` half. That is why the `.js` / `.panel.jsx` split exists — JSX
cannot be imported by node, so behaviour that lived beside a component could not
be tested at all.

To poke at something interactively:

```bash
cd <repository root>
node --input-type=module -e '
import { gridNodes, gridRadius, validateGrid } from "./web/frontend/src/warp.js";
const extent = [-5825, 5825, -3425, 3425];
const src = gridNodes(extent, 7, 7);
console.log(validateGrid(src, src, gridRadius(extent, 7, 7), extent, 7, 7));
'
```

Note the **explicit `.js` extension**: Vite resolves extensionless imports, node
does not. Imports inside `curation/tools/*.js` carry the extension for exactly
this reason.

`tests/test_curation_tools.mjs` shows the pattern for driving a whole gesture
against a fake editor — copy its `makeEditor()` if you want to try a new tool
before wiring any UI.

---

## 3. Running the app while you work on it

Two servers. The backend serves the API; in development Vite serves the frontend
with hot reload and proxies `/api` to the backend.

**Terminal 1 — backend:**

```bash
cd <repository root>
conda activate macat
uvicorn web.server:app --host 127.0.0.1 --port 8700 --reload
```

`--reload` restarts on a Python edit. Bind to `127.0.0.1` rather than `0.0.0.0`
for a dev instance so you are not publishing a half-finished build to the network.

**Terminal 2 — frontend with hot reload:**

```bash
source ~/.nvm/nvm.sh && nvm use 20
cd <repository root>/web/frontend
npm run dev
```

Then open <http://localhost:5173> — **not** 8700. On 5173 a frontend edit appears
without a rebuild; on 8700 you are looking at whatever `npm run build` last wrote
into `web/frontend/dist`, which is the single most common way to waste twenty
minutes on this codebase.

If the backend is on a non-default port:

```bash
VITE_API_TARGET=http://localhost:8701 npm run dev
```

> The proxy default in `vite.config.js` pointed at port **8600** — a leftover
> from the original prototype — until 2026-09-14, so `npm run dev` could not
> reach the API at all. If you are on an older checkout and every request 502s,
> that is why.

### Using the built frontend instead

When you want to test what a user actually gets, skip Vite:

```bash
cd web/frontend && npm run build && cd ../..
# then open http://127.0.0.1:8700
```

Rebuild after **every** frontend change, and hard-reload the browser
(Ctrl-Shift-R) to drop cached assets.

---

## 4. Not writing into your real job store

By default every job — including anything you create while testing — is written
to `pipeline_output/jobs/` alongside real work. Point `ALLEN_OUT_ROOT` somewhere
scratch and the server, the job store, the registration worker and the finalize
worker all follow it; there are no test-only branches in the code:

```bash
export ALLEN_OUT_ROOT=/tmp/macat-scratch
uvicorn web.server:app --host 127.0.0.1 --port 8701 --reload
```

Copy an existing completed job in to get a realistic starting point without
re-running registration:

```bash
mkdir -p "$ALLEN_OUT_ROOT/jobs"
cp -r pipeline_output/jobs/<job-id> "$ALLEN_OUT_ROOT/jobs/"
cp pipeline_output/manual_bundle_<tag>.npz "$ALLEN_OUT_ROOT/"
```

The job's `job.json` records absolute paths to its bundle and output directory;
if you move a job, check those still resolve or the Curation step will report a
missing bundle.

Curation and Results are the cheap steps to iterate on — both read an existing
`manual_bundle*.npz` and neither re-runs registration.

---

## 5. Killing a server you no longer need

On a shared machine, port 8700 may be someone else's server, not yours.
Identify the owner before killing anything.

### Find what is listening

```bash
ss -ltnp | grep -E ':(8700|5173)'        # also shows the owning user
lsof -nP -iTCP:8700 -sTCP:LISTEN         # same, alternative tool
```

Read the `users:(("uvicorn",pid=12345,fd=3))` field and the UID. If it is not
yours, leave it alone and use another port:

```bash
uvicorn web.server:app --host 127.0.0.1 --port 8701 --reload
```

### Stop your own

Gracefully first — `Ctrl-C` in the terminal that owns it, or:

```bash
pkill -f 'uvicorn web.server:app.*8701'     # your dev backend
pkill -f 'vite'                              # the frontend dev server
sleep 2
pgrep -af 'uvicorn web.server:app' || echo 'no backend running'
```

Only if something refuses to exit:

```bash
pkill -9 -f 'uvicorn web.server:app.*8701'
```

Or kill by port, which does not require you to get the pattern right:

```bash
fuser -k 8701/tcp     # your dev backend
fuser -k 5173/tcp     # the Vite dev server
```

> **`pgrep -af 'uvicorn|vite'` will match your own shell command**, because the
> pattern appears in the command line you just typed. If the output looks like
> your own `pgrep`, that is what happened. Match on something specific such as
> `uvicorn web.server:app` instead.

### Orphans

`--reload` runs a supervisor plus a worker, so killing one PID can leave the
other. After any kill, confirm nothing survived:

```bash
pgrep -af 'uvicorn web.server:app'
pgrep -af 'node.*vite'
ss -ltnp | grep -E ':(8700|5173|8701)'
```

A `npm run dev` started from a terminal that has since closed keeps holding 5173.
`fuser -k 5173/tcp` is the reliable way to clear it.

## 6. Traps that cost real time here

1. **Looking at 8700 while editing the frontend.** You are seeing the last
   `npm run build`. Use 5173, or rebuild.
1b. **A 404 for `/favicon.ico` in the backend log is not a fault.** Browsers
   request it unprompted. Both `favicon.ico` and `favicon.svg` now ship in
   `dist/`, so it should be a 200 — if you still see the 404, your `dist/` predates
   that and a rebuild fixes it.
2. **Skipping the parity harness** after touching warp math. See §1.
3. **Monkey-patching the facade.** Since the core was split, patching
   `pipeline_api._load_structure_tree` silently does nothing — a function
   resolves its helpers in its own module. Patch the owning module; see
   `tests/test_depth2_polygons.py` and rule 1c in
   [`repository_layout.md`](repository_layout.md).
4. **Extensionless imports in files node must read.** Vite forgives them; node
   does not.
5. **Assuming a green build means working code.** `npm run build` only proves the
   JSX parses. It will happily build a component that references a variable you
   deleted. The node suites are what actually exercise behaviour.
