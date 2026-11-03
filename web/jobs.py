#!/usr/bin/env python
"""
Unified job state machine for the MERFISH -> Allen React app.

One job == one section going through the whole pipeline. All intermediate state
and transforms are persisted to disk (pipeline_output/jobs/<id>/job.json) so a
job is resumable and its result is reproducible. Heavy steps (registration) run
in a detached subprocess worker; the machine only records their status.

States (linear, with FAILED as a side state):

    INPUT_READY
      -> ORIENTATION_CONFIRMED
      -> AP_CONFIRMED
      -> AUTO_REGISTRATION_DONE
      -> MANUAL_CURATION_CONFIRMED
      -> FINALIZING
      -> COMPLETED   (or FAILED)

The algorithms themselves (registration, polygon export, label assignment, QC)
are NOT reimplemented here — this module only orchestrates pipeline_api.
"""
import os
import sys
import json
import time
import uuid
import tempfile
import subprocess

import config
from allen_alignment import planes as _planes

STATES = [
    "INPUT_READY",
    "ORIENTATION_CONFIRMED",
    "AP_CONFIRMED",
    "AUTO_REGISTRATION_DONE",
    "MANUAL_CURATION_CONFIRMED",
    "FINALIZING",
    "COMPLETED",
    "FAILED",
]

# Single root for EVERYTHING a job writes: its own state (OUT_ROOT/jobs/<id>/) and
# every artifact the workers produce (bundle, fit, curated CSV/GeoJSON/PNG), because
# both workers are handed `job["out_dir"]` and never hard-code a path.
#
# Since 2026-10-28 a new job's artifacts go in its OWN folder,
# OUT_ROOT/jobs/<id>/outputs/ (job_out_dir). Before that every job wrote flat
# into OUT_ROOT under a per-SECTION name, so two jobs on one section overwrote
# each other's bundle, fit and per-cell CSV, and an older job could end up
# showing a newer job's registration. Jobs created before then keep the
# out_dir they recorded; nothing on disk was moved.
#
# ALLEN_OUT_ROOT overrides it. That is what keeps an end-to-end test from writing
# into the real pipeline_output: point the env var at a scratch directory, drop a
# fixture job in it, and the server, the job store, the registration worker and the
# finalize worker all follow -- no test-only branches anywhere in the code.
# Unset (production) it resolves to the historical location, unchanged.
OUT_ROOT = os.path.abspath(
    os.environ.get("ALLEN_OUT_ROOT")
    or os.path.join(config.STUDY_DIR, "pipeline_output"))
JOBS_DIR = os.path.join(OUT_ROOT, "jobs")


# --------------------------------------------------------------------------- io
def _now():
    return time.time()


def job_dir(jid):
    return os.path.join(JOBS_DIR, jid)


def job_out_dir(jid):
    """Where a job's artifacts go: its own folder, so no other job can overwrite
    them. File names inside still carry the section tag, which keeps them
    readable and keeps the download names unchanged."""
    return os.path.join(job_dir(jid), "outputs")


def _job_file(jid):
    return os.path.join(job_dir(jid), "job.json")


def _atomic_write(path, obj):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def load(jid):
    p = _job_file(jid)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def save(job):
    job["updated"] = _now()
    _atomic_write(_job_file(job["id"]), job)
    return job


def list_jobs():
    if not os.path.isdir(JOBS_DIR):
        return []
    out = []
    for jid in os.listdir(JOBS_DIR):
        j = load(jid)
        if j:
            out.append(j)
    out.sort(key=lambda j: j.get("created", 0), reverse=True)
    return out


# Notes live beside job.json, not in it: registration and finalize workers
# rewrite job.json from their own copy, which would drop a note saved mid-run.
MAX_NOTES = 500


def read_notes(jid):
    try:
        with open(os.path.join(job_dir(jid), "notes.json")) as f:
            return str(json.load(f).get("notes") or "")
    except (OSError, ValueError):
        return ""


def set_notes(jid, notes):
    if load(jid) is None:
        raise KeyError(jid)
    notes = str(notes or "").strip()[:MAX_NOTES]
    _atomic_write(os.path.join(job_dir(jid), "notes.json"), {"notes": notes})
    return notes


def _require(job, states):
    if job["state"] not in states:
        raise ValueError(f"job in state {job['state']}, expected one of {states}")


def bundle_ap(bundle_path):
    """Cheaply read just the AP index baked into a registration bundle (without
    loading the heavy label/image arrays)."""
    import numpy as np
    with np.load(bundle_path, allow_pickle=True) as d:
        return int(d["ap"])

def bundle_ap_audit(bundle_path):
    """Return the declared AP plus the slice actually sampled into the bundle."""
    import numpy as np
    with np.load(bundle_path, allow_pickle=True) as d:
        declared = int(d["ap"])
        sampled = int(d["sampled_ap_idx"]) if "sampled_ap_idx" in d else None
        sampled_um = (
            float(d["sampled_ap_um"])
            if "sampled_ap_um" in d
            else float(np.median(np.asarray(d["tf"])[..., 0]))
        )
        return declared, sampled, sampled_um


def bundle_extent(bundle_path):
    """Cheaply read the displayed plane-micron extent (lr0, lr1, si0, si1) of a
    registration bundle -- the same value pipeline_api.warp_grid_extent derives and
    /api/bundle reports, but without loading the heavy label/image arrays. Used to
    validate a client's warp grid BEFORE any job state is touched."""
    import numpy as np
    with np.load(bundle_path, allow_pickle=True) as d:
        xJ0 = d["xJ0"]; xJ1 = d["xJ1"]
        return (float(xJ1[0]), float(xJ1[-1]), float(xJ0[0]), float(xJ0[-1]))


def bundle_res(bundle_path):
    """Cheaply read the plane spacing a bundle was registered on (microns)."""
    import numpy as np
    import pipeline_api as api
    with np.load(bundle_path, allow_pickle=True) as d:
        return api.bundle_res_um(d)


# ------------------------------------------------------------ atlas resolution
# `ap_idx` is an index, and an index means a different place at each atlas
# resolution: 150 is 925 um at 50 um and 300 is 912.5 um at 25 um. So a job also
# records the resolution it was picked at (`ap_res_um`) and the micron value
# (`ap_um`). A job written before those fields was picked at 50 um -- every
# bundle on disk on 2026-10-07 was, which is what this default rests on.
LEGACY_AP_RES_UM = 50.0


def current_res_um():
    return float(config.PLANE_DX_UM)


def ap_res_um(job):
    """The atlas resolution this job's slice index refers to, or None."""
    if job.get("ap_idx") is None:
        return None
    return float(job.get("ap_res_um") or LEGACY_AP_RES_UM)


def _plane(job):
    return _planes.normalize((job.get("orientation") or {}).get("plane"))


def _here_shape():
    import pipeline_api as api
    return tuple(api.get_atlas().shape)


def atlas_shape_at(res):
    """The atlas volume's shape at resolution ``res``, without loading it.

    The physical extent is the same at every resolution (13.2 x 8.0 x 11.4 mm),
    so the voxel count follows from the shape here. Used for reading a job's
    atlas coordinates at the resolution that job was registered at.
    """
    here = current_res_um()
    return tuple(int(round(n * here / float(res))) for n in _here_shape())


def ap_um(job):
    """The job's slice choice in microns on its plane's slice axis, or None.

    For a legacy job this is reconstructed from the index at 50 um. The extent
    of the atlas is the same at every resolution, so the slice count at the old
    resolution follows from the count here.
    """
    if job.get("ap_idx") is None:
        return None
    if job.get("ap_um") is not None:
        return float(job["ap_um"])
    res = ap_res_um(job)
    ax = _planes.slice_axis(_plane(job))
    n_old = round(_here_shape()[ax] * current_res_um() / res)
    return float(job["ap_idx"]) * res - (n_old - 1) * res / 2


def atlas_check(job):
    """Does this job's AP choice, and its bundle, match the server's atlas?

    Computed on read and never stored: nothing about a job changes until
    someone re-picks the slice. ``ap_idx_here`` is the job's choice carried to
    this resolution by microns, for the step-3 slider to start on.
    """
    here = current_res_um()
    out = {"server_res_um": here, "ap_res_um": ap_res_um(job),
           "bundle_res_um": None, "ap_idx_here": None, "ok": True, "reason": None}
    if job.get("ap_idx") is not None:
        if out["ap_res_um"] == here:
            out["ap_idx_here"] = int(job["ap_idx"])
        else:
            coords = _planes.axis_coords(_here_shape(), here)
            out["ap_idx_here"] = _planes.nearest_index(coords, ap_um(job), _plane(job))
    bp = bundle_path(job)
    if bp and os.path.exists(bp):
        try:
            out["bundle_res_um"] = bundle_res(bp)
        except Exception:      # an unreadable bundle is someone else's error to report
            pass
    for what, res in (("registration", out["bundle_res_um"]),
                      ("reference level", out["ap_res_um"])):
        if res is not None and abs(res - here) > 1e-6:
            out["ok"] = False
            out["reason"] = (f"This job's {what} was made on the {res:g} um atlas; "
                             f"the server now runs at {here:g} um.")
            break
    return out


def assert_resolution_consistent(job):
    """Guard for the steps that consume a bundle: refuse one made at another
    resolution with a message that says what to do about it."""
    chk = atlas_check(job)
    if not chk["ok"]:
        import pipeline_api as api
        raise api.ResolutionMismatch(
            chk["reason"] + " Re-pick the reference level and re-run registration.")


def assert_ap_consistent(job):
    """Guard: the registration bundle's AP MUST equal the job's authoritative
    ap_idx before curation/finalize. If they disagree the bundle is stale (AP was
    changed after registration) and continuing would silently mislabel cells at
    the wrong AP while the UI reports the new one. Raise with full audit context."""
    bp = (job.get("register", {}).get("result") or {}).get("bundle_path")
    if not bp or not os.path.exists(bp):
        return  # no bundle yet; the caller's own "run registration first" check applies
    job_ap = job.get("ap_idx")
    b_ap, sampled_ap, sampled_um = bundle_ap_audit(bp)
    if job_ap is None or int(job_ap) != b_ap:
        raise ValueError(
            "AP mismatch: registration bundle is stale — re-run registration. "
            f"job_id={job.get('id')} job_ap={job_ap} bundle_ap={b_ap} "
            f"bundle_path={bp}")
    if sampled_ap is None:
        raise ValueError(
            "AP audit missing: this legacy registration bundle does not record "
            "which annotation slice its content actually sampled. Re-run "
            f"registration. job_id={job.get('id')} job_ap={job_ap} "
            f"bundle_ap={b_ap} sampled_ap_um={sampled_um:.1f} bundle_path={bp}")
    if sampled_ap is not None and sampled_ap != int(job_ap):
        raise ValueError(
            "AP mismatch: bundle metadata names the selected AP but its annotation "
            "content was sampled from a different slice — re-run registration. "
            f"job_id={job.get('id')} job_ap={job_ap} bundle_ap={b_ap} "
            f"sampled_ap={sampled_ap} sampled_ap_um={sampled_um:.1f} "
            f"bundle_path={bp}")


# --------------------------------------------------------------- state machine
# A job NAME is a label: it titles the job in the rail, heads the Results step,
# and names what you download. A job TAG keys the artifacts on disk, and several
# jobs on one section deliberately share those, so
# the tag is derived from the SECTION and never from the label. Renaming a job
# therefore changes what a download is called and nothing else -- it cannot
# orphan an output or split one section's artifacts into two sets.
MAX_JOB_NAME = 120


def safe_name(name, fallback=""):
    """A job label reduced to something usable as a filename.

    This reaches Content-Disposition and zip entry names, so it has to lose
    anything that could steer a path or break a header: separators, dots that
    could start a traversal, quotes, control characters. Everything outside
    [A-Za-z0-9-_] becomes an underscore, runs collapse, and the result is capped.
    """
    cleaned = "".join(c if (c.isalnum() or c in "-_") else "_"
                      for c in str(name or ""))
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_")[:MAX_JOB_NAME]
    return cleaned or fallback


def create(exp_path, region_key=None, plane=None, name=None, source=None, seed=None):
    import pipeline_api as api
    ok, msg = api.validate_region(exp_path)
    if not ok:
        raise ValueError(msg)
    jid = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    auto = region_key or os.path.basename(os.path.normpath(exp_path))
    # The tag follows the SECTION, not the label, so a custom name never moves
    # an artifact. It names files; it no longer has to make them unique, since
    # each job writes into its own folder (job_out_dir).
    tag = "_" + "".join(c if (c.isalnum() or c in "-_") else "_" for c in auto)
    name = (str(name).strip()[:MAX_JOB_NAME] if name and str(name).strip()
            else auto)
    job = {
        "id": jid,
        "state": "INPUT_READY",
        "created": _now(),
        "updated": _now(),
        "exp_path": os.path.abspath(exp_path),
        "region_key": region_key,
        "name": name,
        "tag": tag,
        "out_dir": job_out_dir(jid),
        "validate_msg": msg,
        # Seeded from the region's declared plane so a sagittal dataset does not
        # start life as coronal. Step 2 still shows it and can change it; this
        # only sets what that control opens on.
        # (Until 2026-10-23 a second "orientation": None key, left over from
        # before the seeding, silently overrode this one.) None = no plane
        # declared; step 2 then opens on coronal.
        "orientation": ({"angle": 0.0, "flip": "none",
                         "plane": _planes.normalize(plane)} if plane else None),
        # `seed` (from the section's expected.json, web/answer_key.py) pre-fills
        # step 2 below; the user still confirms or changes it there.
        "ap_idx": None,
        "register": {"status": "idle"},   # status/result/bundle_path/metrics
        "manual": None,               # {R, depth, anchors, result}
        "error": None,
    }
    if seed:
        o = dict(job["orientation"] or {"angle": 0.0, "flip": "none",
                                         "plane": _planes.normalize(None)})
        o.update({k: seed[k] for k in ("plane", "hemisection", "angle", "flip") if k in seed})
        job["orientation"] = o
    if source:
        # where the experiment really lives when exp_path is a local cache of
        # it (web/s3source.py): {"url": "s3://...", "cache": <exp_path>}
        job["source"] = source
    return save(job)


def _invalidate_downstream(job, from_step):
    """Drop every result produced at or after `from_step` so a job can never keep
    a stale registration / curation once an upstream input (orientation or AP)
    changes. Mutates `job` in place; the caller decides the resulting state.

    from_step:
        "ap"       -> the AP changed: registration + curation are stale.
        "orient"   -> orientation changed: AP is still valid as a number, but the
                      registration (which baked in that orientation) + curation
                      are stale and must be re-run.

    Both cases clear the same downstream artifacts today (registration is the
    first heavy step after orientation); the parameter is kept explicit so the
    intent is auditable and future steps can be added without guessing.
    """
    job["register"] = {"status": "idle"}
    job["manual"] = None
    job["error"] = None
    return job


def set_orientation(jid, angle, flip, plane=None, hemisection=None):
    job = load(jid)
    if job is None:
        raise KeyError(jid)
    _require(job, ["INPUT_READY", "ORIENTATION_CONFIRMED", "AP_CONFIRMED",
                   "AUTO_REGISTRATION_DONE", "MANUAL_CURATION_CONFIRMED",
                   "COMPLETED", "FAILED"])
    from allen_alignment import planes as _planes
    # The section plane rides with the orientation because it is the same kind of
    # decision -- how the tissue relates to the atlas -- and because changing it
    # invalidates exactly the same downstream work, which the `changed` rollback
    # below already handles. A job written before this field defaults to coronal.
    plane_ = _planes.normalize(plane if plane is not None
                               else (job.get("orientation") or {}).get("plane"))
    new_o = {"angle": float(angle), "flip": flip or "none", "plane": plane_,
             # None = a whole section. Part of the orientation because it is the
             # same kind of decision and invalidates the same work: the
             # registration is fitted differently for half a brain.
             "hemisection": _planes.normalize_hemisection(hemisection, plane_)}
    # Compare against the STORED orientation with the plane defaulted in. Every
    # job written before this field exists as {angle, flip}, and comparing that
    # to {angle, flip, plane} makes an unchanged re-confirm look like a change --
    # which rolls a COMPLETED job back to ORIENTATION_CONFIRMED and discards its
    # registration and curation. Caught by test_orientation_unchanged_is_noop.
    old_o = dict(job.get("orientation") or {})
    if old_o:
        old_o.setdefault("plane", _planes.DEFAULT)
        # every job before 2026-10-20 was a whole section
        old_o.setdefault("hemisection", None)
    changed = old_o != new_o
    job["orientation"] = new_o
    if changed and job["state"] not in ("INPUT_READY", "ORIENTATION_CONFIRMED"):
        # orientation changed after AP/registration: everything downstream (the
        # registration bundle baked in this orientation, and any curation on top
        # of it) is now stale. Roll back to ORIENTATION_CONFIRMED so the user
        # re-confirms AP and re-runs registration; the previous AP number is kept
        # as the default (the AP slider re-reads job.ap_idx).
        _invalidate_downstream(job, from_step="orient")
        job["state"] = "ORIENTATION_CONFIRMED"
    elif job["state"] == "INPUT_READY":
        job["state"] = "ORIENTATION_CONFIRMED"
    return save(job)


def set_ap(jid, ap_idx):
    job = load(jid)
    if job is None:
        raise KeyError(jid)
    if job.get("orientation") is None:
        raise ValueError("confirm orientation first")
    _require(job, ["ORIENTATION_CONFIRMED", "AP_CONFIRMED", "AUTO_REGISTRATION_DONE",
                   "MANUAL_CURATION_CONFIRMED", "COMPLETED", "FAILED"])
    new_ap = int(ap_idx)
    here = current_res_um()
    coords = _planes.axis_coords(_here_shape(), here)
    n = len(coords[_planes.slice_axis(_plane(job))])
    if not 0 <= new_ap < n:
        raise ValueError(f"slice index {new_ap} is outside the {here:g} um atlas "
                         f"(0..{n - 1} on the {_plane(job)} plane)")
    # Same index at a different resolution is a different slice, so a change of
    # resolution alone counts as a change and invalidates downstream work.
    changed = (job.get("ap_idx") != new_ap or ap_res_um(job) != here)
    job["ap_idx"] = new_ap
    job["ap_res_um"] = here
    job["ap_um"] = _planes.slice_coordinate(coords, new_ap, _plane(job))
    if changed and job["state"] in ("AUTO_REGISTRATION_DONE",
                                    "MANUAL_CURATION_CONFIRMED", "COMPLETED",
                                    "FAILED"):
        # AP changed after registration: the bundle (and any curation on it) was
        # built at the old AP and is now stale. Force a fresh registration so the
        # bundle's AP can never disagree with job.ap_idx.
        _invalidate_downstream(job, from_step="ap")
    # a changed AP always lands the job at AP_CONFIRMED (nothing downstream is
    # valid anymore); an unchanged AP only advances from ORIENTATION_CONFIRMED.
    if changed or job["state"] == "ORIENTATION_CONFIRMED":
        job["state"] = "AP_CONFIRMED"
    return save(job)


def start_register(jid):
    job = load(jid)
    if job is None:
        raise KeyError(jid)
    _require(job, ["AP_CONFIRMED", "AUTO_REGISTRATION_DONE", "FAILED"])
    if job.get("ap_idx") is None or job.get("orientation") is None:
        raise ValueError("orientation and AP must be confirmed first")
    # The worker registers `ap_idx` on the atlas it loads now; an index picked at
    # another resolution would put the section at the wrong place in the brain.
    if ap_res_um(job) != current_res_um():
        raise ValueError(
            f"the reference level was picked on the {ap_res_um(job):g} um atlas and "
            f"the server runs at {current_res_um():g} um; re-confirm it in step 3")
    jd = job_dir(jid)
    os.makedirs(jd, exist_ok=True)
    # reset live status files
    open(os.path.join(jd, "register.log"), "w").close()
    with open(os.path.join(jd, "register.status"), "w") as f:
        f.write("running")
    job["state"] = "AP_CONFIRMED"     # stays here until the worker completes
    job["error"] = None
    job["register"] = {"status": "running", "started": _now()}
    save(job)
    out = open(os.path.join(jd, "register_worker.out"), "w")
    subprocess.Popen(
        [sys.executable, "-m", "web._register_worker", jid],
        cwd=config.STUDY_DIR, stdout=out, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    return load(jid)


def register_status(jid):
    """Live registration status: reloads job.json (the worker updates it on
    completion) plus tails the running log."""
    job = load(jid)
    if job is None:
        raise KeyError(jid)
    jd = job_dir(jid)
    log = []
    lf = os.path.join(jd, "register.log")
    if os.path.exists(lf):
        with open(lf) as f:
            log = f.read().splitlines()
    sf = os.path.join(jd, "register.status")
    status = job.get("register", {}).get("status", "idle")
    if os.path.exists(sf):
        status = open(sf).read().strip() or status
    return {"job": job, "status": status, "log": log[-60:]}


# ------------------------------------------------------------- manual curation
def _finalize_worker(bundle_path, exp, out_dir, tag, affine=None,
                     src=None, dst=None, R=2500.0, warp=None):
    """Run the authoritative label warp in an isolated subprocess (OOM-safe). When
    ``affine`` is given the worker applies the global in-plane affine plus the
    OPTIONAL sparse CSRBF warp grid (``warp``); otherwise it falls back to the
    CSRBF path (used by the archived standalone editor)."""
    with tempfile.TemporaryDirectory() as td:
        args_p = os.path.join(td, "args.json")
        res_p = os.path.join(td, "result.json")
        with open(args_p, "w") as f:
            json.dump({"path": bundle_path, "exp": exp,
                       "src": src or [], "dst": dst or [], "R": float(R),
                       "affine": affine, "warp": warp,
                       "out_dir": out_dir, "tag": tag}, f)
        proc = subprocess.run(
            [sys.executable, "-m", "web._finalize_worker", args_p, res_p],
            cwd=config.STUDY_DIR, capture_output=True, text=True,
        )
        if proc.returncode != 0 or not os.path.exists(res_p):
            tail = (proc.stderr or proc.stdout or "")[-800:]
            raise RuntimeError(f"finalize worker failed (exit {proc.returncode}): {tail}")
        with open(res_p) as f:
            return json.load(f)


_AFFINE_KEYS = ("cx", "cy", "tx", "ty", "rot_deg", "sx", "sy")
_AFFINE_DEFAULT = {"cx": 0.0, "cy": 0.0, "tx": 0.0, "ty": 0.0,
                   "rot_deg": 0.0, "sx": 1.0, "sy": 1.0}


def _clean_warp(warp):
    """Normalise / sanity-check the sparse warp grid from the client. Returns a
    plain-JSON warp dict, or None when there is no actual displacement (so a
    pure-affine confirm never persists a spurious warp). The authoritative
    geometric validation (edge-fixed, displacement cap, Jacobian) runs in the
    finalize worker via pipeline_api.validate_warp_grid -- this only guards the
    shape so a malformed payload fails fast."""
    if not warp:
        return None
    try:
        src = [[float(a), float(b)] for a, b in warp.get("src", [])]
        dst = [[float(a), float(b)] for a, b in warp.get("dst", [])]
    except (TypeError, ValueError):
        raise ValueError("malformed warp grid points")
    if not src or len(src) != len(dst):
        return None
    import pipeline_api as api
    # Persist the grid dimensions the CLIENT actually used. A warp saved at 5x5 must
    # keep validating and restoring at 5x5 even though new curations default to 7x7,
    # so these are never rewritten to the current default.
    rows = int(warp.get("rows") or api.WARP_GRID_ROWS)
    cols = int(warp.get("cols") or api.WARP_GRID_COLS)
    if rows < 3 or cols < 3 or rows > api.WARP_GRID_MAX_DIM or cols > api.WARP_GRID_MAX_DIM:
        raise ValueError("unsupported warp grid dimensions")
    if len(src) != rows * cols:
        raise ValueError(f"warp grid has {len(src)} points, "
                         f"which is not {rows}x{cols}")
    moved = any(abs(s[0] - d[0]) > 1e-6 or abs(s[1] - d[1]) > 1e-6
                for s, d in zip(src, dst))
    if not moved:
        return None
    # Optional per-warp displacement budget (the elastic push tool asks for more
    # than the default). Clamped HERE as well as in warp_max_disp so the persisted
    # job record can never claim a budget the validator would not grant. Absent or
    # unusable -> None, i.e. the historical WARP_MAX_DISP_FRAC.
    disp_frac = warp.get("disp_frac")
    if disp_frac is not None:
        try:
            disp_frac = float(disp_frac)
        except (TypeError, ValueError):
            raise ValueError("malformed warp disp_frac")
        if not (disp_frac > 0):
            disp_frac = None
        else:
            disp_frac = min(disp_frac, api.WARP_MAX_DISP_FRAC_CEILING)
    out = {"algo": str(warp.get("algo", api.WARP_GRID_ALGO)),
           "rows": rows, "cols": cols,
           "R": (float(warp["R"]) if warp.get("R") else None),
           "src": src, "dst": dst}
    if disp_frac is not None:
        out["disp_frac"] = disp_frac
    return out


def _clean_warp_chain(warp):
    """Sanitise a warp payload that may be a single lattice or a stack.

    Both shapes persist: a job confirmed before layering existed stores one
    lattice, and rewriting it on read would rewrite history for no gain. A stack
    is stored as {"layers": [...]} so the two are never ambiguous.
    """
    import pipeline_api as api
    layers = api.warp_layers(warp)
    if not layers:
        return None
    if len(layers) > api.WARP_MAX_LAYERS:
        raise ValueError(
            f"warp has {len(layers)} layers; at most {api.WARP_MAX_LAYERS} are "
            f"accepted")
    cleaned = [_clean_warp(ly) for ly in layers]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return None
    return cleaned[0] if len(cleaned) == 1 else {"layers": cleaned}


def _assert_warp_valid(warp, bundle_path):
    """Run the authoritative geometric validation (edge nodes fixed, displacement
    cap, no fold/over-stretch) BEFORE any job state is mutated, so an unacceptable
    grid is a plain 400 with the reason and the job is left exactly as it was.

    The finalize worker re-validates the same grid with the same function -- that
    stays the last line of defence for the non-web paths -- but it runs inside the
    subprocess, and a failure there lands the job in FAILED. A rejected grid is bad
    client INPUT, not a broken pipeline, so it must never get that far."""
    if not warp:
        return
    import numpy as np
    import pipeline_api as api
    layers = api.warp_layers(warp)
    if not layers:
        return
    if len(layers) > api.WARP_MAX_LAYERS:
        raise ValueError(f"warp has {len(layers)} layers; at most "
                         f"{api.WARP_MAX_LAYERS} are accepted")
    extent = bundle_extent(bundle_path)
    lr0, lr1, si0, si1 = extent
    norm = []
    for n, ly in enumerate(layers):
        rows = int(ly.get("rows") or api.WARP_GRID_ROWS)
        cols = int(ly.get("cols") or api.WARP_GRID_COLS)
        R = ly.get("R")
        if not R:
            sp_lr = (lr1 - lr0) / (cols - 1); sp_si = (si1 - si0) / (rows - 1)
            R = api.WARP_GRID_RADIUS_FACTOR * 0.5 * (sp_lr + sp_si)
        src = np.asarray(ly["src"], float); dst = np.asarray(ly["dst"], float)
        v = api.validate_warp_grid(src, dst, float(R), extent, rows, cols,
                                   max_disp=api.warp_max_disp(
                                       extent, rows, cols, ly.get("disp_frac")),
                                   jac_min=api.warp_jac_min(ly.get("disp_frac")))
        if not v["ok"]:
            where = "" if len(layers) == 1 else f" (layer {n + 1} of {len(layers)})"
            raise ValueError(f"warp grid rejected{where}: {v['reason']}")
        norm.append({"src": src, "dst": dst, "R": float(R)})
    # Determinants multiply, so individually legal layers can still compose into
    # a fold. Checked here too, not only in finalize, so a bad stack is a 400
    # with a reason rather than a job that dies in the worker after confirming.
    cmin, _ = api.warp_composed_jac(norm, extent)
    if cmin <= api.WARP_COMPOSED_JAC_MIN:
        raise ValueError(
            f"the {len(layers)} warp layers are each valid but compose to min "
            f"|J| {cmin:.3f}, at or below the {api.WARP_COMPOSED_JAC_MIN} floor")


def confirm_curation(jid, affine, warp=None):
    """Manual curation confirm: persist the authoritative GLOBAL AFFINE parameters
    plus the OPTIONAL sparse CSRBF warp grid (reproducible) and generate curated
    per-cell labels + region polygons from the LEAF-level annotation resampled by
    affine + warp, advancing the job to COMPLETED.

    ``affine`` = {cx, cy, tx, ty, rot_deg, sx, sy} in plane microns [lr, si];
    ``warp`` = None or {algo, rows, cols, R, src:[[lr,si]..], dst:[[lr,si]..]}.
    The browser preview and this finalize use the identical affine + CSRBF math;
    the warp is RE-VALIDATED in the finalize worker (never trusted blindly)."""
    job = load(jid)
    if job is None:
        raise KeyError(jid)
    # FAILED is retryable: a finalize that died (OOM, a transient worker crash)
    # must not permanently brick a job whose registration bundle is still good.
    _require(job, ["AUTO_REGISTRATION_DONE", "COMPLETED",
                   "MANUAL_CURATION_CONFIRMED", "FAILED"])
    reg = job.get("register", {}).get("result") or {}
    bundle_path = reg.get("bundle_path")
    if not bundle_path or not os.path.exists(bundle_path):
        raise ValueError("no registration bundle; run auto registration first")
    # the bundle's AP must match the authoritative job AP, or the labels would be
    # computed at a stale AP while the UI reports the new one. Resolution first:
    # across a resolution change the AP check fails too, with a less useful message.
    assert_resolution_consistent(job)
    assert_ap_consistent(job)
    exp = job["exp_path"]
    jd = job_dir(jid)
    tag = job["tag"] + "_curated"

    affine = {k: float((affine or {}).get(k, _AFFINE_DEFAULT[k])) for k in _AFFINE_KEYS}
    warp = _clean_warp_chain(warp)
    # reject an unacceptable grid here, while the job is still untouched
    _assert_warp_valid(warp, bundle_path)

    # persist the manual affine parameters + warp grid (re-editable / reproducible)
    params = {"kind": "affine_grid" if warp else "global_affine",
              "affine": affine, "warp": warp,
              "bundle": bundle_path, "ap": job.get("ap_idx")}
    warp_json = os.path.join(jd, "manual_warp.json")
    _atomic_write(warp_json, params)

    job["state"] = "MANUAL_CURATION_CONFIRMED"
    job["manual"] = {"status": "running", "affine": affine, "warp": warp,
                     "warp_json": warp_json}
    save(job)
    job["state"] = "FINALIZING"
    save(job)
    try:
        man = _finalize_worker(bundle_path, exp, job["out_dir"], tag,
                               affine=affine, warp=warp)
    except Exception as e:
        job["state"] = "FAILED"
        job["error"] = str(e)
        job["manual"]["status"] = "failed"
        save(job)
        raise
    job["manual"]["status"] = "done"
    job["manual"]["result"] = man
    job["state"] = "COMPLETED"
    return save(job)


def bundle_path(job):
    return (job.get("register", {}).get("result") or {}).get("bundle_path")


# ---------------------------------------------------------------- deleting jobs
# Jobs on one section SHARE their bundle, fit and per-cell CSVs (the tag follows
# the section, not the job), so deleting a job cannot mean "delete every file it
# names". A file goes only when no job that stays still points at it, and a
# section-named artifact only when no job that stays has that section's tag.
#
# Section artifacts are matched by EXACT name pattern, never by "contains the
# tag": a short tag such as `R1` would otherwise match `..._tr_x_R1.npz` of a
# different section. These are every name the pipeline writes with a tag
# (registration, finalize, figures, region tables, hex QC, Vizualizer export).
_SECTION_ARTIFACT_PREFIXES = (
    "cell_metadata_with_allen_regions", "manual_bundle", "m3_fit_3d",
    "mapped_region_polygons", "region_summary", "region_gene_counts",
    "region_expression_qc", "hex_expression_qc",
    "TO_MERSCOPE_VIZUALIZER_allen_regions",
) + tuple(f"{view}_{name}" for view in ("curated", "register")
          for name in ("result", "regions_over_dapi", "regions_over_markers",
                       "cells_leaf_rank", "cells_leaf_allen"))


def _section_artifact_re(core):
    import re
    return re.compile(
        "^(?:" + "|".join(re.escape(p) for p in _SECTION_ARTIFACT_PREFIXES) + ")_"
        + re.escape(core) + r"(?:_curated)?(?:_\d+um)?(?:_panels)?\.[A-Za-z0-9]+$")


def referenced_files(job):
    """Every existing file a job's registration and curation results point to,
    as real paths. `exp_path` sits outside those blocks: raw data never appears."""
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and x.startswith("/") and os.path.isfile(x):
            found.add(os.path.realpath(x))

    walk({"register": job.get("register"), "manual": job.get("manual")})
    return found


def is_busy(job):
    """A worker is writing to this job right now."""
    return (job.get("state") == "FINALIZING"
            or (job.get("register") or {}).get("status") == "running")


def delete_jobs(ids):
    """Delete jobs and whatever of their output no remaining job needs.

    Raises KeyError for an unknown id and ValueError for a job a worker is
    writing to, BEFORE deleting anything, so a bad selection never half-runs.
    Only files under OUT_ROOT are ever removed; the experiment data, and any
    output a job recorded elsewhere, are left alone.
    """
    import shutil
    ids = list(dict.fromkeys(ids))
    every = {j["id"]: j for j in list_jobs()}
    unknown = [i for i in ids if i not in every]
    if unknown:
        raise KeyError(", ".join(unknown))
    busy = [every[i].get("name") or i for i in ids if is_busy(every[i])]
    if busy:
        raise ValueError("still running, wait for it to finish: " + ", ".join(busy))

    gone = set(ids)
    staying = [j for i, j in every.items() if i not in gone]
    keep = set()
    for j in staying:
        keep |= referenced_files(j)
    staying_tags = {(j.get("tag") or "").lstrip("_") for j in staying}

    root = os.path.realpath(OUT_ROOT)
    jobs_root = os.path.realpath(JOBS_DIR)
    inside = lambda p: p.startswith(root + os.sep) and not p.startswith(jobs_root + os.sep)
    files, kept = set(), set()
    for i in ids:
        job = every[i]
        for f in referenced_files(job):
            if inside(f):
                (kept if f in keep else files).add(f)
        core = (job.get("tag") or "").lstrip("_")
        if core and core not in staying_tags:
            pat = _section_artifact_re(core)
            for n in os.listdir(root):
                p = os.path.realpath(os.path.join(root, n))
                if os.path.isfile(p) and pat.match(n) and p not in keep:
                    files.add(p)

    for i in ids:
        shutil.rmtree(job_dir(i), ignore_errors=True)
    removed = 0
    for f in sorted(files):
        try:
            os.remove(f)
            removed += 1
        except FileNotFoundError:
            pass
    return {"deleted": ids, "files_removed": removed, "files_kept_shared": len(kept)}
