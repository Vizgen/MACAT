#!/usr/bin/env python
"""FastAPI backend for the job-scoped MERFISH-to-Allen web workflow.

The API persists orientation and AP choices, dispatches registration and
finalization workers, serves co-registered DAPI/density/atlas assets, and
exposes downloadable leaf-level annotations and QC outputs.

Run from the repository root with::

    uvicorn web.server:app --host 0.0.0.0 --port 8700
"""
import os
import io
import glob

# NumPy/OpenBLAS is also used by the lightweight pre-finalize validation in this
# long-lived API process.  Keep it single-threaded on the shared server: an
# OpenBLAS worker-thread crash would otherwise take down uvicorn and surface as a
# 502 instead of an ordinary validation/finalize error.
for _thread_env in (
    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
):
    os.environ[_thread_env] = "1"

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.cm as cm
from PIL import Image

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import version
import pipeline_api as api
import web.jobs as jobs
import web.previews as previews
from web import transcript_density
from allen_alignment import markers as markerlib
from allen_alignment import vizualizer
from web import s3source
from web import datafolder
from web import answer_key

STUDY = config.STUDY_DIR
# Bundle discovery follows jobs.OUT_ROOT so an isolated test root sees ONLY its own
# fixtures and can never resolve a production bundle. With ALLEN_OUT_ROOT unset this
# is exactly the historical list.
_IS_ISOLATED = jobs.OUT_ROOT != os.path.join(STUDY, "pipeline_output")
SEARCH_DIRS = ([jobs.OUT_ROOT] if _IS_ISOLATED else
               [os.path.join(STUDY, d)
                for d in ("pipeline_output", "_gpu_validate", "m3_output")])
# manual-curation boundary modes. `depth2` is the DEFAULT read-only overlay
# (literal structure-tree depth 2, official Allen colours); the older coarse/
# landmark sets and the dense ontology depths are debug/reference only.
# NOTE the two different senses of "landmark" here. The mode the UI labels
# **Landmarks** is "alignment": four hand-picked structures (HPF, VL, V3, TH) at
# four different Allen depths, drawn as sparse guides. The mode literally named
# "landmarks" is an older, API-only tier that rolls every leaf up to
# ["Isocortex", "HPF", "TH", "VS"]. They are not the same thing and no button
# exposes the second one.
BOUNDARY_MODES = ["alignment", "depth2", "depth3", "depth4",
                  "coarse", "landmarks", "major", "medium", "full"]
BOUNDARY_DEFAULT = "depth2"
_DEBUG_DEPTH = {"medium": 5, "full": None}   # ontology depth for debug views

app = FastAPI(title="MACAT — MERFISH Allen CCF Alignment Tool")


@app.on_event("startup")
def _warn_missing_reference_files():
    """Say at startup, not at the first registration, when the Allen reference
    files have not been built: registration cannot run without them."""
    from allen_alignment.reference_files import HOW_TO_BUILD, missing
    gone = missing()
    if gone:
        import logging
        logging.getLogger("uvicorn.error").warning(
            "Allen reference files missing:\n  %s\n%s",
            "\n  ".join(gone), HOW_TO_BUILD)


app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

_bundles = {}          # path -> (fingerprint, bundle dict)
_boundaries = {}       # (path, fingerprint, depth_key) -> cached identity polylines
_region_labels = {}    # (path, fingerprint, depth_key) -> cached interior acronym anchors
_dapi_plane = {}       # (path, fingerprint, supersample) -> plane-aligned DAPI PNG
_transcript_plane = {} # (bundle path/fp, transcript path/fp) -> density PNG bytes
_marker_plane = {}     # (bundle path/fp, cell_by_gene fp) -> (PNG bytes, coverage)
_qc_cache = {}         # (mapped_csv path, fingerprint) -> biological-QC result dict
_region_exports_cache = {}  # (mapped_csv, fingerprint) -> region-level exports result dict
_hex_qc_cache = {}      # mapped/cell-by-gene fingerprints + bin parameters -> result
_section_ref = {}       # the ABC section x gene table, loaded once
_section_q_cache = {}   # (cell_by_gene path, fingerprint) -> per-gene query profile


def _fp(path):
    """Content fingerprint (mtime_ns, size) so a same-named file that was
    overwritten in place (a re-run registration bundle, a re-finalized CSV) is
    treated as a NEW cache entry — no server restart needed."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _find_bundles():
    found = []
    for d in SEARCH_DIRS:
        found += glob.glob(os.path.join(d, "manual_bundle*.npz"))
        # per-job outputs (jobs/<id>/outputs/, since 2026-10-28)
        found += glob.glob(os.path.join(d, "jobs", "*", "outputs", "manual_bundle*.npz"))
    return sorted(set(found))


def _resolve(path):
    if not path:
        bs = _find_bundles()
        if not bs:
            raise HTTPException(404, "no manual_bundle*.npz found; run a registration first")
        path = bs[0]
    if not os.path.exists(path):
        raise HTTPException(404, f"bundle not found: {path}")
    return path


def _load(path):
    fp = _fp(path)
    ent = _bundles.get(path)
    if ent is None or ent[0] != fp:
        _bundles[path] = (fp, api.load_bundle(path))
    return _bundles[path][1]


def _cached_boundaries(path, mode):
    if mode not in BOUNDARY_MODES:
        raise HTTPException(400, f"unknown boundary mode {mode}")
    key = (path, _fp(path), mode)
    if key not in _boundaries:
        b = _load(path)
        if mode == "alignment":
            _boundaries[key] = api.alignment_region_boundaries(b)
        elif mode in ("depth2", "depth3", "depth4"):
            _boundaries[key] = api.depth2_boundaries(b, level=int(mode[-1]))
        elif mode in ("coarse", "landmarks", "major"):
            _boundaries[key] = api.landmark_boundaries(b, tier=mode)
        else:
            _boundaries[key] = api.bundle_boundaries(b, depth=_DEBUG_DEPTH[mode])
    return _boundaries[key]


def _cached_labels(path, mode):
    """Interior acronym anchors for the major coarse regions (only the coarse-type
    tiers carry a landmark grouping; debug tiers get none)."""
    key = (path, _fp(path), mode)
    if key not in _region_labels:
        if mode == "alignment":
            _region_labels[key] = []
        elif mode in ("depth2", "depth3", "depth4"):
            _region_labels[key] = api.depth2_region_labels(
                _load(path), level=int(mode[-1]))
        elif mode in ("coarse", "landmarks", "major"):
            _region_labels[key] = api.region_labels(_load(path), tier=mode)
        else:
            _region_labels[key] = []
    return _region_labels[key]



def _tag(path):
    return os.path.basename(path).replace("manual_bundle", "").replace(".npz", "")


@app.get("/api/atlas_meta")
def atlas_meta():
    """Atlas identity, plus the step-3 slider for EACH plane.

    The slider's bounds, start and micron scale all come from here: the axis it
    indexes has 264 slices coronally and 228 sagittally at 50 um, twice that at
    25 um, and any constant in the client addresses the wrong range on at least
    one of those four."""
    from allen_alignment import planes as _planes
    meta = dict(api.atlas_provenance())
    shape = api.get_atlas().shape
    meta["planes"] = {p: {**_planes.slider(shape, api.RES, p),
                          **_planes.LABELS[p]} for p in _planes.PLANES}
    meta["default_plane"] = _planes.DEFAULT
    return meta


@app.get("/api/bundle")
def bundle_meta(path: str = ""):
    path = _resolve(path)
    b = _load(path)
    xJ0 = b["xJ0"]; xJ1 = b["xJ1"]; J = b["Jimg"]
    return {
        "path": path,
        "tag": _tag(path),
        # Content fingerprint of the bundle, for cache-busting the images that
        # are DERIVED from it. Several jobs on one section share a single
        # manual_bundle_<tag>.npz and each registration overwrites it in place,
        # so the URL of a DAPI or marker image is stable while its content is
        # not. The server caches correctly (it keys on this same fingerprint);
        # the BROWSER does not, because those responses carry max-age=3600.
        # Keying the query on this is exact: the image changes iff the bundle
        # does. A job timestamp is not enough -- another job's registration can
        # overwrite the bundle without touching this job at all, which is the
        # case that produced a stale, visibly shifted background in Results.
        "fp": (lambda f: f"{f[0]}-{f[1]}" if f else "")(_fp(path)),
        "ap": int(b["ap"]),
        "selected_ap_um": (
            float(b["selected_ap_um"]) if "selected_ap_um" in b else None),
        "sampled_ap_idx": (
            int(b["sampled_ap_idx"]) if "sampled_ap_idx" in b else None),
        "sampled_ap_um": (
            float(b["sampled_ap_um"]) if "sampled_ap_um" in b else
            float(np.median(np.asarray(b["tf"])[..., 0]))),
        "exp_path": str(b["exp_path"]) if "exp_path" in b else "",
        "atlas_provenance": b.get("atlas_provenance", api.atlas_provenance()),
        # plane-micron extent: lr left/right, si top/bottom (si increases downward)
        "lr0": float(xJ1[0]), "lr1": float(xJ1[-1]),
        "si0": float(xJ0[0]), "si1": float(xJ0[-1]),
        "width": int(J.shape[1]), "height": int(J.shape[0]),
        "depths": list(BOUNDARY_MODES),
        "depth_default": BOUNDARY_DEFAULT,
        # can /api/dapi.png serve a plane-aligned DAPI background for this job?
        # (needs the mosaic AND the micron->pixel transform); the curation step
        # hides the background toggle and falls back to density when false.
        "has_dapi": bool(previews.has_dapi(
            str(b["exp_path"]) if "exp_path" in b else "")),
        "has_markers": _marker_ready(b),
        "has_transcripts": bool(transcript_density.find_table(
            str(b["exp_path"]) if "exp_path" in b else "")),
    }


@app.get("/api/density.png")
def density_png(path: str = ""):
    path = _resolve(path)
    # auto-contrast shared with the result figure's no-DAPI fallback
    # (figures.plane_density). Grayscale, dense = white: magma's red-yellow
    # competed with the coloured region overlay during curation (2026-10-21).
    from allen_alignment.figures import plane_density
    rgba = (cm.gray(plane_density(_load(path))) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/transcript_density.png")
def transcript_density_png(path: str = ""):
    """All-gene transcript density on the same plane grid as DAPI and cells.

    The source table is streamed in bounded batches. The rendered PNG is cached
    using both bundle and transcript-table fingerprints, so later requests do no
    table I/O unless either input changed.
    """
    path = _resolve(path)
    bundle = _load(path)
    exp_path = str(bundle["exp_path"]) if "exp_path" in bundle else ""
    transcript_path = transcript_density.find_table(exp_path)
    if transcript_path is None:
        raise HTTPException(404, "no detected_transcripts CSV or Parquet for this experiment")
    key = (path, _fp(path), transcript_path, _fp(transcript_path))
    ent = _transcript_plane.get(key)
    if ent is None:
        try:
            density = transcript_density.on_plane(bundle, transcript_path)
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(422, f"cannot render transcript density: {exc}") from exc
        shown = np.log1p(density)
        positive = shown[shown > 0]
        hi = float(np.percentile(positive, 99.5)) if positive.size else 1.0
        rgba = (cm.magma(np.clip(shown / max(hi, 1e-12), 0, 1)) * 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
        ent = _transcript_plane[key] = buf.getvalue()
    return Response(ent, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


def _marker_ready(bundle):
    """True when this experiment's panel supports at least one division."""
    exp = str(bundle["exp_path"]) if "exp_path" in bundle else ""
    cbg = os.path.join(exp, "cell_by_gene.csv")
    if not exp or not os.path.exists(cbg):
        return False
    try:
        import pandas as pd
        cols = pd.read_csv(cbg, nrows=0).columns
        return any(r["used"] for r in markerlib.match_panel(cols))
    except Exception:
        return False


@app.get("/api/markers")
def markers_report(path: str = ""):
    """Which anatomical divisions this panel can support, and on which genes.

    The browser shows this as the legend for the marker background, so a reviewer
    can see that e.g. Cerebellum is drawn from four genes and Ventricular systems
    from three -- the layer should be read with that in mind rather than as an
    equally-confident statement about every division.
    """
    import pandas as pd
    bundle = _load(_resolve(path))
    exp = str(bundle["exp_path"]) if "exp_path" in bundle else ""
    cbg = os.path.join(exp, "cell_by_gene.csv")
    if not os.path.exists(cbg):
        raise HTTPException(404, "no cell_by_gene.csv for this experiment")
    cols = pd.read_csv(cbg, nrows=0).columns
    return {"min_genes": markerlib.MIN_GENES_PER_REGION,
            "regions": markerlib.match_panel(cols)}


@app.get("/api/marker_density.png")
def marker_density_png(path: str = "", regions: str = ""):
    """Anatomical marker-gene composite on the same plane grid as DAPI and cells.

    Each coarse Allen division is drawn in ITS OWN ALLEN COLOUR -- the colour the
    atlas overlay already uses -- so marker signal and atlas outline are directly
    comparable and a misalignment reads as a colour offset. Cached on the bundle
    and cell_by_gene fingerprints, because the join is the expensive part.
    """
    path = _resolve(path)
    bundle = _load(path)
    exp = str(bundle["exp_path"]) if "exp_path" in bundle else ""
    cbg = os.path.join(exp, "cell_by_gene.csv")
    if not os.path.exists(cbg):
        raise HTTPException(404, "no cell_by_gene.csv for this experiment")
    # `regions` is the legend selection, empty meaning "every division this panel
    # supports". Part of the cache key, so toggling a division off and back on is
    # served from memory rather than re-reading cell_by_gene.
    only = sorted({r.strip() for r in regions.split(",") if r.strip()}) or None
    key = (path, _fp(path), cbg, _fp(cbg), tuple(only or ()))
    ent = _marker_plane.get(key)
    if ent is None:
        try:
            planes, report = markerlib.region_planes(bundle, exp, only=only)
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(422, f"cannot render marker background: {exc}") from exc
        rgba = markerlib.composite_rgba(planes, report)
        if rgba is None:
            raise HTTPException(
                422, "no anatomical division has enough markers in this panel")
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
        ent = _marker_plane[key] = buf.getvalue()
    return Response(ent, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/dapi.png")
def dapi_png(path: str = "", supersample: int = 3):
    """DAPI resampled onto the SAME plane grid and drawn over the SAME extent as
    /api/density.png, so the curation background can be switched between the two
    without the atlas overlay moving. 404 when the experiment has no DAPI mosaic /
    micron->pixel transform (the client hides the toggle in that case)."""
    path = _resolve(path)
    key = (path, _fp(path), int(supersample))
    ent = _dapi_plane.get(key)
    if ent is None:
        img = previews.dapi_on_plane(_load(path), supersample=int(supersample))
        if img is None:
            raise HTTPException(404, "no DAPI mosaic for this experiment")
        g = (np.clip(img, 0, 1) * 255).astype(np.uint8)
        buf = io.BytesIO()
        # A greyscale PNG is visually identical when drawn to canvas, but avoids
        # storing/transmitting three duplicate colour channels plus opaque alpha.
        Image.fromarray(g, "L").save(buf, "PNG", optimize=True)
        ent = _dapi_plane[key] = buf.getvalue()
    return Response(ent, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/boundaries")
def boundaries(path: str = "", depth: str = BOUNDARY_DEFAULT):
    path = _resolve(path)
    if depth not in BOUNDARY_MODES:
        raise HTTPException(400, f"unknown boundary mode {depth}")
    return {"depth": depth, "features": _cached_boundaries(path, depth),
            "labels": _cached_labels(path, depth)}


def _download_name(job, path):
    """What a file should be CALLED when downloaded, given the job's label.

    Artifacts are stored under the section's tag, because jobs on one section
    share them. A job's name is a label on top of that, so the rename happens on
    the way OUT: the tag is swapped for the label in the basename and nothing on
    disk moves. When the label is still the default the two are equal and the
    filename is byte-identical to what it has always been.
    """
    base = os.path.basename(path)
    if not job:
        return base
    core = (job.get("tag") or "").lstrip("_")
    label = jobs.safe_name(job.get("name"), core)
    if not core or label == core:
        return base
    # the tag as a whole token, once: a bare `replace` also rewrote it INSIDE
    # other words, so tag "t" turned "_curated" into "_curamy_sectioned"
    import re
    tok = re.compile(r"(?<![A-Za-z0-9])" + re.escape(core) + r"(?![A-Za-z0-9])")
    return tok.sub(label, base, count=1) if tok.search(base) else f"{label}_{base}"


@app.get("/api/download")
def download(path: str, job: str = ""):
    if not os.path.exists(path):
        raise HTTPException(404, "file not found")
    # only serve files under the study output dirs
    if not any(os.path.abspath(path).startswith(os.path.abspath(d)) for d in SEARCH_DIRS):
        raise HTTPException(403, "path not allowed")
    # `job` only renames the download; it never widens what may be served, so a
    # bad or missing id degrades to the on-disk name rather than to an error.
    j = jobs.load(job) if job else None
    return FileResponse(path, filename=_download_name(j, path))


# ============================================================ job state machine
# The React app drives one job through the whole pipeline; the backend persists
# every intermediate state + transform so a job is resumable and reproducible.

class JobCreateReq(BaseModel):
    exp_path: str = ""
    # Optional label. Blank falls back to the folder name, which is what the
    # field is prefilled with, so the default is unchanged.
    name: str = ""
    # for an s3:// exp_path: also fetch the DAPI mosaic (~12-63 GB) in the
    # background. Off by default; the app falls back to cell density without it.
    fetch_dapi: bool = False


class OrientReq(BaseModel):
    angle: float = 0.0
    flip: str = "none"
    # "coronal" (default) or "sagittal"; chosen in the orientation step because
    # it is the same class of decision and invalidates the same downstream work.
    plane: str = ""
    # "" or "whole" for a whole section; "left"/"right" for a coronal section
    # that is one half of the brain, the half as displayed (dorsal up)
    hemisection: str = ""


class APReq(BaseModel):
    ap_idx: int


class RegisterReq(BaseModel):
    # empty: the registration has no tunable iterations since the LDDMM path was
    # removed (2026-10-27). Older clients that still send `niter` are accepted;
    # pydantic ignores the extra field.
    pass


class CurationReq(BaseModel):
    # authoritative GLOBAL AFFINE parameters (plane microns [lr, si]); the browser
    # preview and the backend finalize build the identical 2x3 matrix from these.
    affine: dict = {}
    # OPTIONAL sparse Wendland-CSRBF warp grid applied AFTER the affine:
    # {algo, rows, cols, R, src:[[lr,si]..], dst:[[lr,si]..]}. Re-validated by the
    # finalize worker (pipeline_api.validate_warp_grid); never trusted blindly.
    warp: dict | None = None


def _known_level(job):
    """The job's known atlas level at THIS server's resolution, read from the
    answer key in its own folder (expected.json; web/answer_key.py), or None.
    Read on every view rather than stored, so it follows a resolution switch."""
    return answer_key.known_level(answer_key.read(job.get("exp_path")),
                                  jobs.current_res_um())


@app.post("/api/jobs")
def create_job(req: JobCreateReq):
    exp = datafolder.clean_path(req.exp_path)
    if not exp:
        raise HTTPException(400, "exp_path required")
    if s3source.is_s3(exp):
        # Fetch what the app reads into a local cache and build the job on that
        # folder; record where it came from. The big files follow in the
        # background. See web/s3source.py.
        url = exp.strip()
        try:
            cached, _ = s3source.prepare(url, include_dapi=req.fetch_dapi)
            job = jobs.create(cached, s3source.auto_key(url), None,
                              name=req.name or None,
                              source={"url": url, "cache": cached},
                              seed=answer_key.orientation(answer_key.read(cached)))
        except s3source.NetworkError as e:
            raise HTTPException(503, str(e))         # the server's network: retry
        except ValueError as e:
            raise HTTPException(400, str(e))
        s3source.start_background(cached)
        return _with_atlas_check(job)
    try:
        # A section that carries an answer key (expected.json) pre-fills step 2
        # with its plane, half, rotation and flip; anything else starts coronal
        # until step 2 says otherwise.
        return jobs.create(exp, None, None, name=req.name or None,
                           seed=answer_key.orientation(answer_key.read(exp)))
    except ValueError as e:
        raise HTTPException(400, str(e))


def _server_time(ts):
    """`ts` as "2026-10-07 16:23" on the SERVER's clock and time zone.

    This is the clock `jobs.create` stamps the job ID with (`time.strftime`, the
    server's local time), so a row's created time and its job ID -- its folder
    name -- always agree, whatever zone the person viewing the page is in."""
    import time
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _server_tz():
    import time
    return time.strftime("%Z")


def _with_atlas_check(j):
    """A job as the client sees it: plus whether it matches the server's atlas,
    and its created time already formatted on the server's clock.

    Derived on every read rather than stored, so switching the server's
    resolution changes what a job reports without rewriting any job.json."""
    out = {**j, "atlas_check": jobs.atlas_check(j), "notes": jobs.read_notes(j["id"])}
    known = _known_level(j)
    if known:
        out["known_level"] = known
    seeded = answer_key.orientation(answer_key.read(j.get("exp_path")))
    if seeded:
        out["answer_orientation"] = seeded      # what step 2 was pre-filled with
    src = j.get("source")
    if src and src.get("cache"):
        # live: the background fetch updates this record as files arrive
        rec = s3source.read_record(src["cache"]) or {}
        out["source_files"] = {k: v.get("status") for k, v in rec.get("files", {}).items()}
    if isinstance(j.get("created"), (int, float)):
        out["created_text"] = _server_time(j["created"])
    return out


@app.exception_handler(api.ResolutionMismatch)
def _resolution_mismatch(request, exc):
    # 409, not 400: the request was fine, the job's registration is what is out
    # of date. Covers every bundle read, including the image endpoints.
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/api/jobs")
def list_all_jobs():
    return [_with_atlas_check(j) for j in jobs.list_jobs()]


class DeleteJobsReq(BaseModel):
    ids: list[str]


@app.post("/api/jobs/delete")
def delete_jobs(req: DeleteJobsReq):
    """Delete jobs from the store. All-or-nothing on the checks: an unknown id
    (404) or a job a worker is writing to (409) deletes nothing. Files another
    job still uses are kept; see jobs.delete_jobs."""
    if not req.ids:
        raise HTTPException(400, "no jobs given")
    try:
        return jobs.delete_jobs(req.ids)
    except KeyError as e:
        raise HTTPException(404, f"job not found: {e.args[0]}")
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- the user's data folder (step 1, "Your sections"); see web/datafolder.py ----
class DataFolderReq(BaseModel):
    folder: str = ""


def _local_only(request):
    """Folder listings show the file system, so only the machine MACAT runs on
    (directly or through an SSH tunnel) may see or change them."""
    host = request.client.host if request.client else ""
    if not datafolder.is_local(host):
        raise HTTPException(403, "the data folder can only be viewed or set on the "
                                 "computer MACAT runs on (or through an SSH tunnel)")


def _data_folder_call(fn, *a):
    try:
        return fn(*a)
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except s3source.NetworkError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/data_folder")
def data_folder(request: Request):
    """The data folder and its last scan. Never scans by itself (except once
    for a folder set by MACAT_DATA_DIR that has not been scanned yet)."""
    host = request.client.host if request.client else ""
    if not datafolder.is_local(host):
        return {"available": False,
                "reason": "The data folder can only be set on the computer MACAT runs on."}
    return {"available": True, **_data_folder_call(datafolder.current)}


@app.post("/api/data_folder")
def set_data_folder(req: DataFolderReq, request: Request):
    """Set the data folder (blank clears it) and scan it."""
    _local_only(request)
    if not datafolder.clean_path(req.folder):
        return {"available": True, **_data_folder_call(datafolder.clear)}
    return {"available": True, **_data_folder_call(datafolder.set_folder, req.folder)}


@app.post("/api/data_folder/rescan")
def rescan_data_folder(request: Request):
    _local_only(request)
    return {"available": True, **_data_folder_call(datafolder.rescan)}


@app.get("/api/jobs_dir")
def jobs_dir():
    """For the notes under the Resume list: where job folders live (follows
    ALLEN_OUT_ROOT, so it is the real location on any host), and the server's
    time zone, which the Created time column is shown in."""
    return {"jobs_dir": jobs.JOBS_DIR, "server_tz": _server_tz()}


@app.get("/api/reports.zip")
def reports_zip(ids: str):
    """Several jobs' report.zip in one download: one inner archive per COMPLETED
    job, each exactly what that job's own button gives. Jobs not completed are
    skipped -- the page has already said which, and how many will come.

    Written to a temporary file rather than held in memory: a report is ~50 MB,
    and a selection of twenty should not cost a gigabyte of RAM. The inner
    archives are already compressed, so they are stored, not deflated again.
    """
    import tempfile
    import time
    import zipfile
    from starlette.background import BackgroundTask
    wanted = [i for i in dict.fromkeys(ids.split(",")) if i]
    picked = [j for j in (jobs.load(i) for i in wanted)
              if j is not None and j.get("state") == "COMPLETED"]
    if not picked:
        raise HTTPException(400, "none of the selected jobs is completed")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    names = set()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for j in picked:
            name = f"{jobs.safe_name(j.get('name'), j['id'])}_report.zip"
            if name in names:                 # two jobs with one label
                name = f"{jobs.safe_name(j.get('name'), j['id'])}_{j['id']}_report.zip"
            names.add(name)
            z.writestr(name, job_report_zip(j["id"]).body)
    stamp = time.strftime("%Y%m%d_%H%M")
    return FileResponse(tmp, media_type="application/zip",
                        filename=f"MAAT_reports_{len(picked)}_jobs_{stamp}.zip",
                        background=BackgroundTask(os.remove, tmp))


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    return _with_atlas_check(j)


def _job_transition(fn, *a):
    try:
        return _with_atlas_check(fn(*a))
    except KeyError:
        raise HTTPException(404, "job not found")
    except api.ResolutionMismatch as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{jid}/orientation")
def job_orientation(jid: str, req: OrientReq):
    return _job_transition(jobs.set_orientation, jid, req.angle, req.flip,
                           req.plane or None, req.hemisection or None)


class NotesReq(BaseModel):
    notes: str = ""


@app.post("/api/jobs/{jid}/notes")
def job_notes(jid: str, req: NotesReq):
    """Free-text notes from the Resume list. Any state, even while running."""
    try:
        return {"id": jid, "notes": jobs.set_notes(jid, req.notes)}
    except KeyError:
        raise HTTPException(404, "job not found")


@app.post("/api/jobs/{jid}/ap")
def job_ap(jid: str, req: APReq):
    return _job_transition(jobs.set_ap, jid, req.ap_idx)


@app.post("/api/jobs/{jid}/register")
def job_register(jid: str, req: RegisterReq):
    return _job_transition(jobs.start_register, jid)


@app.get("/api/jobs/{jid}/register_status")
def job_register_status(jid: str):
    try:
        return jobs.register_status(jid)
    except KeyError:
        raise HTTPException(404, "job not found")


@app.post("/api/jobs/{jid}/curation")
def job_curation(jid: str, req: CurationReq):
    return _job_transition(jobs.confirm_curation, jid, req.affine, req.warp)


@app.get("/api/jobs/{jid}/base.png")
def job_base_png(jid: str, max_px: int = 1600):
    """Un-rotated DAPI base; the client applies rotate/flip live via CSS.
    max_px picks the render resolution (clamped 400..2000 in previews)."""
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    png = previews.render_base_png(j["exp_path"], max_px=max_px)
    # The un-oriented base image is immutable for a job. InputStep preloads this
    # exact URL before entering Orientation, so allow the browser to reuse the
    # decoded response instead of rendering/downloading it a second time.
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/atlas.png")
def atlas_png(idx: int = 150, min_px: int = previews.ATLAS_MIN_PX,
              plane: str = ""):
    """Atlas slice for the AP step. `min_px` upsamples the natively ~228x160 CCF
    slice so it fills the stage instead of sitting tiny next to the tissue.
    `plane` selects which axis `idx` indexes -- coronal (default) or sagittal."""
    return Response(previews.render_atlas_png(idx, min_px=min_px, plane=plane),
                    media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{jid}/transform")
def job_transform(jid: str):
    """The confirmed manual transform, for redrawing the curated overlay live.

    Results needs the same `place()` the curation canvas uses -- global affine
    then local CSRBF -- because the curated annotation raster is never saved: the
    bundle holds the AUTOMATIC `AphiL`, and the curated result is that plus this
    transform. Returning it lets Results draw the curated boundaries from the
    same `/api/boundaries` the curation step already serves, rather than needing
    a second raster.
    """
    import json as _json
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    man = j.get("manual") or {}
    reg = (j.get("register") or {}).get("result") or {}
    warp = None
    wj = man.get("warp_json")
    if wj and os.path.exists(wj):
        try:
            with open(wj) as fh:
                warp = (_json.load(fh) or {}).get("warp")
        except Exception:
            warp = None
    # A curation may have stacked several lattices. They are returned as an
    # ordered list so the client folds them in the same order finalize does;
    # `warp` stays for the single-lattice case every older job carries, and so a
    # client that has not been rebuilt keeps working on those.
    layers = api.warp_layers(warp)
    return {"available": bool(reg.get("bundle_path")),
            "bundle_path": reg.get("bundle_path"),
            # absent when the job was finalized with a pure affine, which is the
            # common case; the client then applies the affine alone
            "affine": man.get("affine"),
            "warp": layers[0] if len(layers) == 1 else None,
            "warp_layers": layers,
            "plane": (j.get("orientation") or {}).get("plane") or "coronal"}


_cells_cache = {}   # (csv|bundle, fingerprint, which) -> payload dict


@app.get("/api/jobs/{jid}/cells")
def job_cells(jid: str, which: str = "curated"):
    """Per-cell positions and region ids for the interactive panels.

    Quantised to uint16 over the section's own bounding box: ~0.2 um precision on
    an 11 mm section, far below the ~22 um cell spacing, so nothing is lost and
    the payload is a third of float32. Measured on a real job: 85,346 cells,
    0.5 MB raw and 0.3 MB gzipped -- less than the marker PNG already shipped.

    Coordinates come from the BUNDLE's `xr`/`yr` and are shifted the way
    `_curated_result_fig` shifts them, so the interactive panel and the static
    one are in the same frame and can be compared directly.

    `which` picks the alignment: "curated" takes region ids from the finalized
    per-cell CSV, "auto" reads them back out of the bundle's un-warped `AphiL`.
    """
    import base64
    import numpy as np
    import pandas as pd
    from allen_alignment.atlas import _load_structure_tree
    from allen_alignment.figures import _leaf_result_color

    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    reg = (j.get("register") or {}).get("result") or {}
    man = (j.get("manual") or {}).get("result") or {}
    bundle_path = reg.get("bundle_path")
    if not bundle_path or not os.path.exists(bundle_path):
        return {"available": False, "reason": "no registration bundle yet."}
    src = man.get("csv_path") if which == "curated" else bundle_path
    if which == "curated" and (not src or not os.path.exists(src)):
        return {"available": False, "reason": "this job has not been curated yet."}
    key = (src, _fp(src), which)
    if key in _cells_cache:
        return _cells_cache[key]

    b = api.load_bundle(bundle_path)
    xr = np.asarray(b["xr"], float); yr = np.asarray(b["yr"], float)
    xJ0 = np.asarray(b["xJ0"], float); xJ1 = np.asarray(b["xJ1"], float)
    ext = [xJ1[0], xJ1[-1], xJ0[-1], xJ0[0]]
    px = xr - (float(b["c0"]) - ext[0])
    py = yr - (float(b["r0"]) - ext[3])

    if which == "curated":
        rid = pd.read_csv(src, usecols=["allen_region_id"])[
            "allen_region_id"].to_numpy()
    else:
        A = np.asarray(b["AphiL"])
        row = np.asarray(b["row"]).astype(np.int64)
        col = np.asarray(b["col"]).astype(np.int64)
        inb = np.asarray(b["inb"]).astype(bool)
        keep = (inb & (row >= 0) & (row < A.shape[0])
                & (col >= 0) & (col < A.shape[1]))
        rid = np.zeros(len(row), dtype=np.int64)
        rid[keep] = A[row[keep], col[keep]]
    if len(rid) != len(px):
        return {"available": False,
                "reason": f"cell count mismatch: {len(rid)} labels, {len(px)} positions."}

    x0, x1 = float(px.min()), float(px.max())
    y0, y1 = float(py.min()), float(py.max())
    # ONE scale for both axes. Quantising each to its own 0..65535 range forced
    # the aspect ratio to 1.0 and stretched this 10667 x 6072 um section
    # vertically by 1.76x. The long axis fills the range; the short one uses only
    # part of it, and `qextent` tells the client how much so it can fit correctly.
    span = max(x1 - x0, y1 - y0, 1e-9)
    q = lambda v, lo: np.clip((v - lo) / span * 65535.0, 0, 65535).astype(np.uint16)
    qw = int(round((x1 - x0) / span * 65535))
    qh = int(round((y1 - y0) / span * 65535))

    # index 0 is reserved for "no region", so a palette lookup never has to
    # special-case it and the client can skip drawing it with one comparison
    uids, counts = np.unique(rid[rid > 0], return_counts=True)
    order = np.argsort(-counts)
    uids, counts = uids[order], counts[order]
    lut = {int(u): i + 1 for i, u in enumerate(uids)}
    idx = np.zeros(len(rid), np.uint16)
    for u, i in lut.items():
        idx[rid == u] = i

    tree = _load_structure_tree()
    regions = [{"index": i + 1, "id": int(u), "count": int(c),
                "acronym": (tree.get(int(u)) or {}).get("acronym", str(int(u))),
                "name": (tree.get(int(u)) or {}).get("name", ""),
                "allen": (tree.get(int(u)) or {}).get("color", "#8aa0b8"),
                "rank": _leaf_result_color(i)}
               for i, (u, c) in enumerate(zip(uids, counts))]

    res = {
        "available": True, "which": which, "n": int(len(px)),
        "bounds": [x0, x1, y0, y1],
        # the quantised extent, so the client fits the real shape rather than a square
        "qextent": [qw, qh],
        "um_per_unit": span / 65535.0,
        "x": base64.b64encode(q(px, x0).tobytes()).decode(),
        "y": base64.b64encode(q(py, y0).tobytes()).decode(),
        "r": base64.b64encode(idx.tobytes()).decode(),
        "regions": regions,
        "unlabelled": int((rid <= 0).sum()),
    }
    _cells_cache[key] = res
    return res


_auto_fig_cache = {}   # (bundle path, fingerprint) -> {"png", "zip"}


@app.post("/api/jobs/{jid}/auto_figure")
def job_auto_figure(jid: str):
    """Render the AUTOMATIC result figure on demand, and cache it.

    Registration stopped drawing this on 2026-09-22: it cost ~10 s of a ~16 s
    registration and ~40 MB per job, on a set only looked at when someone
    explicitly compares against the curated result. The "vs automatic" coverage
    number in Results never came from it, so nothing routine regressed.

    Everything needed is in the saved bundle, so this is a pure re-render with no
    re-registration. Cached on the bundle's content fingerprint, the same shape
    the region exports and hex QC use, so a second request is free and a re-run
    registration invalidates it.
    """
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    reg = (j.get("register") or {}).get("result") or {}
    bundle = reg.get("bundle_path")
    if not bundle or not os.path.exists(bundle):
        return {"available": False,
                "reason": "no registration bundle for this job yet."}
    key = (bundle, _fp(bundle))
    if key in _auto_fig_cache:
        return {"available": True, "cached": True, **_auto_fig_cache[key]}
    try:
        figs = api.render_auto_result(bundle, j["out_dir"], j["tag"],
                                      atlas=api.get_atlas())
    except Exception as exc:
        raise HTTPException(
            422, f"could not render the automatic figure: {exc}") from exc
    if not figs.get("png"):
        return {"available": False, "reason": "the figure could not be built."}
    out = {"png": figs.get("png"), "zip": figs.get("zip")}
    _auto_fig_cache[key] = out
    # persist so the panel survives a reload and reaches report.zip
    j = jobs.load(jid)
    j.setdefault("register", {}).setdefault("result", {})
    j["register"]["result"]["fig_path"] = out["png"]
    j["register"]["result"]["figure_zip_path"] = out["zip"]
    jobs.save(j)
    return {"available": True, "cached": False, **out}


def _atlas_provenance(man, reg):
    """Which atlas a job's fit used, for report.zip's manifest. job.json has
    carried it since v0.6.0; for older jobs it is read from the registration
    bundle, which has always stored it."""
    prov = man.get("atlas_provenance") or reg.get("atlas_provenance")
    if prov:
        return prov
    path = reg.get("bundle_path")
    if path and os.path.exists(path):
        try:
            with np.load(path, allow_pickle=True) as d:   # our own bundle
                if "atlas_provenance" in d.files:
                    return d["atlas_provenance"].item()
        except (OSError, ValueError):
            pass
    return None


@app.get("/api/jobs/{jid}/report.zip")
def job_report_zip(jid: str):
    """Every deliverable this job produced, in one archive.

    Collected rather than curated: the per-cell CSVs, the region tables, the
    GeoJSON, both figure sets and the hex-bin outputs, plus a manifest.json
    recording what the job was and which files are present. Built on demand and
    streamed from memory -- these are already on disk, and writing a second copy
    of ~60 MB per download to keep would be worse than rebuilding it.

    Missing files are skipped rather than failing the download: a job with no
    DAPI has no DAPI panel, and an archive that refuses to build because an
    optional artifact is absent is less useful than one that says what it has.
    """
    # json and time are not module-level imports in this file; io is.
    import json
    import time
    import zipfile
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    man = (j.get("manual") or {}).get("result") or {}
    reg = (j.get("register") or {}).get("result") or {}
    # Region exports and hex QC are computed on demand and cached in memory, not
    # stored on the job, so they have to be ASKED for rather than read off it.
    # Both are cached on the mapped-CSV fingerprint, so this is free once Results
    # has been opened and computes them otherwise -- which is the right behaviour
    # for a "download everything" button. Neither may fail the archive.
    try:
        exp = job_region_exports(jid) or {}
    except Exception:
        exp = {}
    try:
        hexqc = job_hex_qc(jid) or {}
    except Exception:
        hexqc = {}
    try:
        viz_csv = _vizualizer_csv(j)
    except Exception:
        viz_csv = None

    # (path, name-inside-the-archive). Grouped into folders so the archive is
    # navigable rather than forty files in a heap.
    wanted = []
    # Which alignment each entry describes, recorded AS IT IS ADDED rather than
    # inferred from the filename afterwards. Inferring got it wrong: the
    # automatic per-cell CSV is distinguished from the curated one only by the
    # ABSENCE of a "_curated" suffix, so a name-sniffing rule read it as
    # belonging to neither view.
    describes = {}

    def add(path, folder, view):
        if path and os.path.exists(path):
            # Entries carry the job's LABEL, like every other download, so an
            # extracted folder is identifiable without opening the manifest.
            name = f"{folder}/{_download_name(j, path)}"
            wanted.append((path, name))
            describes[name] = view

    # The region tables and the hex QC are derived from ONE per-cell CSV --
    # `job_region_exports` and `job_hex_qc` both take `man.csv_path or
    # reg.csv_path` -- so they describe the curated alignment on a curated job
    # and the automatic one otherwise. Their filenames are keyed on the BASE tag
    # and say neither, so in a folder called "region_tables" a reader had no way
    # to tell which alignment they were reading. The folder names it.
    derived = _derived_source(j)[2]

    add(man.get("csv_path"), "per_cell", "curated")
    add(reg.get("csv_path"), "per_cell", "auto")
    add(man.get("geojson_path"), "geometry_curated", "curated")
    add(man.get("fig_path"), "figures_curated", "curated")
    add(reg.get("fig_path"), "figures_auto", "auto")
    for key in ("summary", "gene_counts", "qc"):
        add((exp.get("paths") or {}).get(key), f"region_tables_{derived}", derived)
    for key in ("png", "csv"):
        add((hexqc.get("paths") or {}).get(key), f"hex_bin_qc_{derived}", derived)
    # cell -> Allen region, in the only shape the MERSCOPE Vizualizer imports
    add(viz_csv, f"merscope_vizualizer_{derived}", derived)
    # the per-panel figure sets, unpacked from their own zips so the report has
    # one level of archive rather than zips inside a zip
    panel_zips = [(man.get("figure_zip_path"), "figures_curated", "curated"),
                  (reg.get("figure_zip_path"), "figures_auto", "auto")]

    manifest = {
        "job_id": j.get("id"), "name": j.get("name"), "state": j.get("state"),
        "exp_path": j.get("exp_path"), "region_key": j.get("region_key"),
        "ap_idx": j.get("ap_idx"), "orientation": j.get("orientation"),
        "coverage": man.get("coverage", reg.get("coverage")),
        "n_mapped": man.get("n_mapped", reg.get("n_mapped")),
        "n_total": man.get("n_total"),
        "auto_coverage": reg.get("coverage"),
        "atlas": _atlas_provenance(man, reg),
        # where the experiment came from when it was read from S3: the URL and,
        # per file, size and ETag as listed when the job was created
        "source": ((s3source.read_record(j["source"]["cache"]) or j["source"])
                   if j.get("source") else None),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        # WHICH CODE produced this. The manifest already said which experiment,
        # which atlas and which AP slice; without this, two archives with
        # identical inputs could differ because the labelling logic changed
        # between them and nothing in either one would say so. Read `dirty`
        # first: when true, the run is not reproducible from `git_sha` alone.
        "software": version.build_info(),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # A zip may legally hold two entries under one name, and most tools then
        # extract one and drop the other silently. The combined figure reaches
        # this archive twice -- once as `fig_path` above, and again inside the
        # panel zip, which is unpacked into the SAME folder -- so without this
        # set it was stored twice, 7 MB each, for both views.
        written = set()

        def put(name, write):
            if name in written:
                return
            written.add(name)
            write(name)

        for src, name in wanted:
            put(name, lambda n, s=src: z.write(s, n))
        for zp, folder, view in panel_zips:
            if not zp or not os.path.exists(zp):
                continue
            with zipfile.ZipFile(zp) as inner:
                for n in inner.namelist():
                    target = f"{folder}/{_download_name(j, n)}"
                    describes.setdefault(target, view)
                    put(target, lambda t, i=inner, k=n: z.writestr(t, i.read(k)))
        # everything actually in the archive, not just the directly-added files:
        # the manifest used to omit the unpacked panels entirely
        manifest["contents"] = sorted(written)
        # Which alignment each file describes. The archive distinguishes the two
        # views by folder, except in per_cell/, where the curated CSV carries a
        # "_curated" suffix and the automatic one is simply the name without it
        # -- readable only if you already know the convention. This states it.
        manifest["describes"] = {n: describes[n] for n in sorted(written)
                                 if n in describes}
        manifest["region_tables_and_hex_qc_derived_from"] = derived
        if viz_csv:
            manifest["merscope_vizualizer"] = {
                "derived_from": derived,
                "format": "one row per EntityID; every column categorical "
                          "text. ontology_level_1..N colour cells by Allen "
                          "hierarchy depth; 'unmapped' and 'beyond atlas' are "
                          "explicit categories."}
        z.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
    buf.seek(0)
    stem = jobs.safe_name(j.get("name"), jid)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="{stem}_report.zip"'})


# ---------------------------------------------------------------- section QC
# One reference per section plane. Both are re-slices of the SAME Allen point
# cloud -- the release was acquired coronally, but every cell carries CCF
# coordinates, so sagittal bands are that cloud cut the other way.
SECTION_REFS = {
    "coronal":  (os.path.join(config.REFERENCE_DATA_DIR,
                              "allen_abc_section_by_gene.csv"), "ap_mm", "AP"),
    "sagittal": (os.path.join(config.REFERENCE_DATA_DIR,
                              "allen_abc_sagittal_by_gene.csv"), "ml_mm", "ML"),
}
# Genes below this in BOTH profiles carry no information and dominate the point
# count; the skill's bulk-correlation step drops the equivalent with min_fpkm.
_CORR_EPS = 1e-4


def _load_section_ref(plane):
    """The ABC reference for one section plane, or None if it is not installed.

    Built by scripts/data/fetch_abc_section_reference.py. Held in a module-level
    dict rather than re-read per request -- each is ~0.5 MB and every job on that
    plane wants it.
    """
    import pandas as pd
    if plane not in _section_ref:
        path, coord, axis = SECTION_REFS[plane]
        if not os.path.exists(path):
            _section_ref[plane] = None
        else:
            df = pd.read_csv(path, index_col=0)
            _section_ref[plane] = {
                "coord": df.pop(coord).to_numpy(float),
                "n": df.pop("n_cells").to_numpy(float),
                "df": df, "axis": axis, "coord_name": coord,
                "lower": {g.lower(): g for g in df.columns},
            }
    return _section_ref[plane]


def _query_profile(cbg_path):
    """Mean counts per cell, per panel gene, for one experiment.

    Mean rather than total so the number printed beside a point means something
    on its own; the correlation itself is unaffected, since a global scale factor
    is a constant shift in log space.
    """
    import pandas as pd
    key = (cbg_path, _fp(cbg_path))
    if key in _section_q_cache:
        return _section_q_cache[key]
    cbg = pd.read_csv(cbg_path, index_col=0)
    genes = [c for c in cbg.columns
             if not c.lower().startswith(("blank", "notarget"))]
    prof = (cbg[genes].sum(0) / max(len(cbg), 1)).astype(float)
    res = {"profile": prof, "n_cells": int(len(cbg)), "n_panel": len(genes)}
    _section_q_cache[key] = res
    return res


@app.get("/api/jobs/{jid}/section_corr")
def job_section_corr(jid: str):
    """Whole-section expression against the Allen reference at the same AP.

    This is the TISSUE-level check, and a different question from the per-region
    specificity below it: not "is each region labelled as itself" but "does this
    section look like brain at all, at the AP we registered it to". It needs no
    registration and no labels -- only the panel counts -- so it is the one QC
    that is independent of everything the alignment did, which is exactly what
    makes it worth showing first.

    Method follows the spatial-transcriptomics skill's transcript QC: log10
    Pearson over shared gene symbols, the same comparison it makes between two
    MERFISH experiments on overlapping panels.

    Each plane has its OWN reference, both re-slices of the same Allen point
    cloud: 53 real coronal sections on the AP axis, and 200 um sagittal bands cut
    along ML. A sagittal query is therefore compared against a sagittal reference
    at its own ML, exactly as a coronal one is compared at its own AP -- not
    against a pooled average, which would be no section at all.

    Also returns r against EVERY reference slice on that plane. That curve peaks
    where the expression matches, which is an estimate owing nothing to the DAPI
    silhouette the registration used -- so a peak far from the confirmed level is
    independent evidence worth surfacing.
    """
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    plane = (j.get("orientation") or {}).get("plane") or "coronal"
    if plane not in SECTION_REFS:
        return {"available": False, "reason": f"unknown section plane {plane!r}."}
    R = _load_section_ref(plane)
    if R is None:
        return {"available": False,
                "reason": f"the Allen {plane} reference is not installed on this "
                          f"server ({os.path.basename(SECTION_REFS[plane][0])} — "
                          f"see docs/reference_provenance.md)."}
    ref = R["df"]
    cbg = os.path.join(j["exp_path"], "cell_by_gene.csv")
    if not os.path.exists(cbg):
        return {"available": False,
                "reason": "no cell_by_gene.csv in the experiment folder."}
    ap_idx = j.get("ap_idx")
    if ap_idx is None:
        return {"available": False,
                "reason": "no reference level confirmed yet — finish step 3."}

    q = _query_profile(cbg)
    prof = q["profile"]
    qlower = {g.lower(): g for g in prof.index}
    rlower = R["lower"]
    common = sorted(set(qlower) & set(rlower))
    if len(common) < 20:
        return {"available": False,
                "reason": f"only {len(common)} gene symbols are shared with the "
                          f"Allen panel; too few to correlate."}

    qv = prof[[qlower[g] for g in common]].to_numpy(float)
    M = ref[[rlower[g] for g in common]].to_numpy(float)
    coord = R["coord"]

    def corr(rv):
        m = (qv > 0) | (rv > 0)
        if m.sum() < 10:
            return None
        c = np.corrcoef(np.log10(qv[m] + _CORR_EPS), np.log10(rv[m] + _CORR_EPS))
        return None if not np.isfinite(c[0, 1]) else float(c[0, 1])

    # `ap_idx` is the index along whatever axis THIS plane holds constant --
    # atlas axis 0 for coronal, axis 2 for sagittal -- and both axes are the same
    # grid measured from the same corner as the Allen CCF coordinates, so one
    # conversion serves both. The grid is the one the index was PICKED on, which
    # is not necessarily the one the server runs now.
    query_mm = float(ap_idx) * jobs.ap_res_um(j) / 1000.0

    profile = [{"mm": float(coord[i]), "section": str(ref.index[i]),
                "r": corr(M[i])} for i in range(len(ref))]
    scored = [p for p in profile if p["r"] is not None]
    best = max(scored, key=lambda p: p["r"]) if scored else None

    k = int(np.argmin(np.abs(coord - query_mm)))
    rv = M[k]
    r = corr(rv)

    keep = (qv > 0) | (rv > 0)
    points = [{"g": common[i], "q": float(qv[i]), "r": float(rv[i])}
              for i in range(len(common)) if keep[i]]
    return {"available": True, "r": r, "n_genes": int(keep.sum()),
            "n_shared": len(common), "n_panel": q["n_panel"],
            "n_cells": q["n_cells"], "plane": plane, "axis": R["axis"],
            "query_mm": query_mm, "ap_idx": int(ap_idx),
            "ref_section": str(ref.index[k]), "ref_mm": float(coord[k]),
            "ref_n_cells": int(R["n"][k]),
            "best": best, "profile": profile, "points": points}


@app.get("/api/jobs/{jid}/qc")
def job_qc(jid: str):
    """AP-independent biological QC: correlate each mapped region's expression
    against the Allen ABC reference (same audit the Streamlit app shows). Needs
    the finalized/auto per-cell CSV + a cell_by_gene.csv in the experiment
    folder. Computed on demand, cached per mapped CSV."""
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    man = (j.get("manual") or {}).get("result") or {}
    reg = (j.get("register") or {}).get("result") or {}
    mapped_csv = man.get("csv_path") or reg.get("csv_path")
    if not mapped_csv or not os.path.exists(mapped_csv):
        return {"available": False, "reason": "no mapped per-cell CSV yet — "
                "run registration (and optionally curation) first."}
    cbg = os.path.join(j["exp_path"], "cell_by_gene.csv")
    if not os.path.exists(cbg):
        return {"available": False, "reason": "no cell_by_gene.csv in the "
                "experiment folder — coverage reflects completeness only, "
                "not localization accuracy."}
    qc_key = (mapped_csv, _fp(mapped_csv))
    if qc_key in _qc_cache:
        return _qc_cache[qc_key]
    try:
        from allen_alignment.qc import m4_abc_audit as qc
        row = qc.audit_paths(mapped_csv, cbg)
    except Exception as e:
        raise HTTPException(500, f"QC failed: {e}")
    if row is None:
        res = {"available": False, "reason": "too few regions shared with the "
               "Allen ABC reference to score."}
        _qc_cache[qc_key] = res
        return res
    conf = row.get("confusions")
    confusions = ([] if conf is None or not len(conf) else
                  [{"region": r["region"], "self_corr": float(r["self_corr"]),
                    "best_match": r["best_match"], "best_corr": float(r["best_corr"]),
                    # how decisively the other region won, and how many cells the
                    # profile rests on -- both change how a row should be read
                    "gap": float(r["gap"]),
                    # NaN != NaN, so this needs no pandas import in this scope
                    "n_cells": (int(r["n_cells"])
                                if "n_cells" in r and r["n_cells"] == r["n_cells"]
                                else None)}
                   for _, r in conf.iterrows()])
    # Every scored region, for the paginated table. Same fields as a confusion
    # row plus self_is_best, which is what marks a row as confusable.
    scored_df = row.get("scored")
    scored = ([] if scored_df is None or not len(scored_df) else
              [{"region": r["region"], "self_corr": float(r["self_corr"]),
                "best_match": r["best_match"], "best_corr": float(r["best_corr"]),
                "gap": float(r["gap"]), "self_is_best": bool(r["self_is_best"]),
                "n_cells": (int(r["n_cells"])
                            if "n_cells" in r and r["n_cells"] == r["n_cells"]
                            else None)}
               for _, r in scored_df.iterrows()])
    res = {"available": True, "verdict": row["verdict"], "scored": scored,
           "median_self_corr": float(row["median_self_corr"]),
           "self_is_best": int(row["self_is_best"]),
           "n_regions": int(row["n_regions"]), "n_genes": int(row["n_genes"]),
           "confusions": confusions}
    _qc_cache[qc_key] = res
    return res


def _derived_source(job):
    """Which per-cell CSV the derived tables are computed from, and the tag to
    name them with.

    The region tables and the hex QC are derived from ONE per-cell CSV --
    curated when a job has one, automatic otherwise -- but they used to be named
    with the job's BASE tag, which says neither. Because these land flat in
    `out_dir` keyed on that tag, a curated job and an auto-only job on the same
    section wrote DIFFERENT CONTENT to the SAME filename, and whichever ran last
    won. (Since 2026-10-28 each new job has its own out_dir, so jobs no longer
    collide at all; within one job the marker still keeps auto and curated
    tables apart.) Measured on a real section: all three region tables differed.

    The per-cell CSVs themselves never had this problem, because finalize marks
    the curated one with `_curated`. So the fix is to carry the same marker the
    source CSV carries, which restores the repo's standing invariant that a
    given artifact name holds a given content.

    This does NOT make every name unique, and is not meant to. Two curated runs
    on one section still share a name -- but so do the per-cell CSVs they are
    computed from, which is the documented "a re-run overwrites rather than
    accumulating". The bug was that the tables disagreed with their own source
    about whether curation had happened.

    Returns (mapped_csv, tag, view). `mapped_csv` may be None.
    """
    man = (job.get("manual") or {}).get("result") or {}
    reg = (job.get("register") or {}).get("result") or {}
    curated = bool(man.get("csv_path"))
    mapped_csv = man.get("csv_path") if curated else reg.get("csv_path")
    tag = (job["tag"].lstrip("_") or job["name"])
    return mapped_csv, (tag + "_curated" if curated else tag), \
        ("curated" if curated else "auto")


def _vizualizer_csv(job):
    """The MERSCOPE Vizualizer table for a job, built on first request.

    Derived from the same per-cell CSV as the region tables (`_derived_source`:
    curated when the job is curated), and written next to it under a name that
    follows it, so it carries `_curated` exactly when its source does. Rebuilt
    only when the source is newer, so a re-finalize is picked up and repeat
    downloads cost nothing. Built on demand rather than at finalize so that every
    job that has a per-cell CSV gets one, including jobs whose bundle this server
    can no longer open. Returns the path, or None when there is no source.
    """
    src = _derived_source(job)[0]
    if not src or not os.path.exists(src):
        return None
    out = os.path.join(os.path.dirname(src), vizualizer.output_name(src))
    if (os.path.exists(out)
            and os.stat(out).st_mtime_ns >= os.stat(src).st_mtime_ns):
        return out
    # the resolution the job was registered at, which is what its per-cell
    # atlas coordinates are measured on -- not necessarily the server's
    res = jobs.ap_res_um(job) or jobs.current_res_um()
    vizualizer.write(src, out, atlas_shape=jobs.atlas_shape_at(res), res=res)
    return out


@app.get("/api/jobs/{jid}/vizualizer.csv")
def job_vizualizer_csv(jid: str):
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    try:
        path = _vizualizer_csv(j)
    except ValueError as e:
        # the source CSV broke one of the format's checks; say which
        raise HTTPException(422, f"cannot build the Vizualizer table: {e}")
    if path is None:
        raise HTTPException(404, "this job has no per-cell labels yet")
    return FileResponse(path, media_type="text/csv",
                        filename=_download_name(j, path))


@app.get("/api/jobs/{jid}/region_exports")
def job_region_exports(jid: str):
    """Region-level deliverables agreed in review (same as the Streamlit
    'Region-level QC & exports', which the React step now titles
    'Per-region agreement'): region_summary / region_gene_counts /
    region_expression_qc CSVs + mapped_region_polygons GeoJSON.

    Uses the `cells` (cell_by_gene) source so it works with the web pipeline's
    affine-only fits (the transcripts/polygon path needs a diffeomorphic fit,
    which the web registration does not save). The polygons deliverable reuses
    the curated GeoJSON produced at finalize (already in the original MERFISH
    micron frame). Computed on demand, cached per mapped CSV."""
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    reg = (j.get("register") or {}).get("result") or {}
    man = (j.get("manual") or {}).get("result") or {}
    mapped_csv, export_tag, _view = _derived_source(j)
    if not mapped_csv or not os.path.exists(mapped_csv):
        return {"available": False, "reason": "no mapped per-cell CSV yet — "
                "run registration first."}
    cbg = os.path.join(j["exp_path"], "cell_by_gene.csv")
    if not os.path.exists(cbg):
        return {"available": False, "reason": "no cell_by_gene.csv in the "
                "experiment folder — region×gene counts and expression QC need it."}
    exports_key = (mapped_csv, _fp(mapped_csv))
    if exports_key in _region_exports_cache:
        return _region_exports_cache[exports_key]
    try:
        from allen_alignment.qc import m4_region_qc as rqc
        out = rqc.run_region_qc(None, tag=export_tag, outdir=j["out_dir"], source="cells",
                                with_polygons=False, mapped_csv=mapped_csv,
                                exp_path=j["exp_path"])
    except Exception as e:
        raise HTTPException(500, f"region exports failed: {e}")
    qc = out["qc"]
    paths = {k: v for k, v in out["paths"].items() if v and os.path.exists(v)}
    gj = man.get("geojson_path")               # curated polygons from finalize
    if gj and os.path.exists(gj):
        paths["polygons"] = gj
    res = {"available": True, "source": out["source"],
           "n_regions": int(len(qc)),
           "n_pass": int((qc["qc_status"] == "PASS").sum()),
           "paths": paths}
    _region_exports_cache[exports_key] = res
    return res


@app.get("/api/jobs/{jid}/hex_qc")
def job_hex_qc(jid: str, hex_size_um: float = 100.0, min_cells: int = 10):
    """Spatial expression-support map on fixed MERFISH-frame hexagonal bins.

    This is an exploratory QC view, not a registration PASS/FAIL gate.  Each
    sufficiently populated bin is correlated with the Allen ABC expression
    profile of its plurality mapped region and with all candidate regions.
    """
    if not (100.0 <= float(hex_size_um) <= 1000.0):
        raise HTTPException(400, "hex_size_um must be between 100 and 1000")
    if not (5 <= int(min_cells) <= 500):
        raise HTTPException(400, "min_cells must be between 5 and 500")
    j = jobs.load(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    mapped_csv, export_tag, _view = _derived_source(j)
    if not mapped_csv or not os.path.exists(mapped_csv):
        return {"available": False, "reason": "no mapped per-cell CSV yet — "
                "run registration first."}
    cbg = os.path.join(j["exp_path"], "cell_by_gene.csv")
    if not os.path.exists(cbg):
        return {"available": False, "reason": "no cell_by_gene.csv in the "
                "experiment folder — hex-bin expression QC needs per-cell counts."}
    metadata = os.path.join(j["exp_path"], "cell_metadata.csv")
    from allen_alignment.qc import m4_hex_qc as hqc
    if not os.path.exists(hqc.ABC):
        from allen_alignment.reference_files import HOW_TO_BUILD
        return {"available": False, "reason": "Allen ABC expression reference "
                "is not installed on this server. " + HOW_TO_BUILD}
    size = float(hex_size_um)
    minimum = int(min_cells)
    # Draw the map in the job's dorsal-up frame so it can be compared directly
    # with the Results figures. Scoring is unaffected (raw-frame binning), but
    # the PNG differs, so the orientation belongs in the cache key.
    o = j.get("orientation") or {}
    angle = float(o.get("angle") or 0.0)
    flip = None if o.get("flip") in (None, "", "none") else o.get("flip")
    key = (mapped_csv, _fp(mapped_csv), cbg, _fp(cbg), hqc.ABC, _fp(hqc.ABC),
           round(size, 3), minimum, round(angle, 3), flip)
    if key in _hex_qc_cache:
        return _hex_qc_cache[key]
    try:
        out = hqc.run_hex_qc(
            mapped_csv, cbg, j["out_dir"], export_tag,
            size_um=size, min_cells=minimum, metadata_csv=metadata,
            angle=angle, flip=flip,
        )
    except Exception as e:
        raise HTTPException(500, f"hex-bin QC failed: {e}")
    res = {
        "available": True,
        "n_bins": out["n_bins"], "n_scored": out["n_scored"],
        "n_cells": out["n_cells"], "n_genes": out["n_genes"],
        "median_self_corr": out["median_self_corr"],
        "best_match_agreement": out["best_match_agreement"],
        "hex_size_um": out["hex_size_um"], "min_cells": out["min_cells"],
        "paths": out["paths"],
    }
    _hex_qc_cache[key] = res
    return res


# serve the built React app (frontend/dist) if present, at /
_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="app")
