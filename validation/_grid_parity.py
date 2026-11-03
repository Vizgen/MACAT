"""Manual-Curation local warp-GRID validation suite (Phase 2).

Checks that the browser grid (web/frontend/src/warp.js, run under node) and the
authoritative backend (pipeline_api) agree on:

  1  the lattice itself      -> warp_grid_nodes / gridNodes
  2  the support radius R    -> warp_grid_radius / gridRadius
  3  which nodes are fixed   -> warp_grid_edge_mask / edgeMask
  4  the shared constants    -> WARP_* vs the warp.js exports
  5  the FORWARD CSRBF field -> warp_csrbf_points / forwardWarp  (per-point)
  6  the validation verdict  -> validate_warp_grid / validateGrid (ok, reason,
                               n_moved, max_disp, min/max Jacobian det)

Run: python validation/_grid_parity.py
"""
import os
import sys

_pre = os.path.join(sys.prefix, "lib", "libstdc++.so.6")
if os.path.exists(_pre) and _pre not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = _pre + (
        ":" + os.environ["LD_PRELOAD"] if os.environ.get("LD_PRELOAD") else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)

import json
import subprocess
import tempfile

import numpy as np

STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, STUDY)
import pipeline_api as api  # noqa: E402

TOL = 1e-6          # microns: cross-language floating-point noise
_fail = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        _fail.append(name)


# The extent of a real bundle when one is available (so the numbers are the ones
# the app actually uses), else a synthetic extent of the same order.
import glob  # noqa: E402
bundles = sorted(glob.glob(os.path.join(STUDY, "pipeline_output", "manual_bundle*.npz")))
if bundles:
    _b = api.load_bundle(bundles[0])
    EXTENT = list(api.warp_grid_extent(_b))
    src_py = api.warp_grid_nodes(_b)
    R_py = api.warp_grid_radius(_b)
    print(f"extent from {os.path.basename(bundles[0])}: "
          f"{[round(v, 1) for v in EXTENT]}")
else:
    EXTENT = [-5775.0, 5775.0, -3475.0, 3475.0]
    lr0, lr1, si0, si1 = EXTENT
    lrs = np.linspace(lr0, lr1, api.WARP_GRID_COLS)
    sis = np.linspace(si0, si1, api.WARP_GRID_ROWS)
    src_py = np.array([[lr, si] for si in sis for lr in lrs], float)
    sp_lr = (lr1 - lr0) / (api.WARP_GRID_COLS - 1)
    sp_si = (si1 - si0) / (api.WARP_GRID_ROWS - 1)
    R_py = api.WARP_GRID_RADIUS_FACTOR * 0.5 * (sp_lr + sp_si)
    print(f"no bundle found; synthetic extent {EXTENT}")

ROWS, COLS = api.WARP_GRID_ROWS, api.WARP_GRID_COLS
CENTRE = (ROWS // 2) * COLS + COLS // 2

# probe points: nodes, cell centres, corners, off-grid interior
PTS = np.vstack([
    src_py,
    [[0.0, 0.0], [1234.5, -678.9], [-4000.0, 3000.0], [500.0, 500.0]],
    [[EXTENT[0], EXTENT[2]], [EXTENT[1], EXTENT[3]]],
])

# ---- cases: identity, one node, two nodes, a legal maximum, three illegal ones ---
sp_min = min((EXTENT[1] - EXTENT[0]) / (COLS - 1), (EXTENT[3] - EXTENT[2]) / (ROWS - 1))
cap = api.WARP_MAX_DISP_FRAC * sp_min


def _dst(moves):
    d = src_py.copy()
    for i, (dlr, dsi) in moves.items():
        d[i] = d[i] + [dlr, dsi]
    return d


def _fold_moves():
    """Two adjacent interior nodes pushed into each other along the grid's tighter
    axis (where the displacement cap allows enough closure to actually fold)."""
    sp_lr = (EXTENT[1] - EXTENT[0]) / (COLS - 1)
    sp_si = (EXTENT[3] - EXTENT[2]) / (ROWS - 1)
    if sp_lr <= sp_si:
        return {CENTRE - 1: (cap * 0.999, 0.0), CENTRE: (-cap * 0.999, 0.0)}
    return {CENTRE - COLS: (0.0, cap * 0.999), CENTRE: (0.0, -cap * 0.999)}


CASES = [
    ("identity",        _dst({})),
    ("centre_node",     _dst({CENTRE: (300.0, 200.0)})),
    ("two_nodes",       _dst({CENTRE: (250.0, -150.0), CENTRE + 1: (-200.0, 120.0)})),
    ("at_disp_cap",     _dst({CENTRE: (cap * 0.98, 0.0)})),
    ("over_disp_cap",   _dst({CENTRE: (cap * 1.20, 0.0)})),
    ("edge_node_moved", _dst({0: (60.0, 0.0)})),
    # a genuine FOLD that the displacement cap alone does NOT catch: two adjacent
    # interior nodes driven into each other, each still within the per-node cap,
    # drive the forward Jacobian negative. The cap is a fraction of the SMALLER
    # node spacing, so the pair has to be adjacent along the grid's tighter axis —
    # across the wider spacing the cap cannot close enough of the gap.
    ("fold",            _dst(_fold_moves())),
]

# --- constrained-edge cases: the DOF rules must match on both sides -------------
_kinds = api.warp_node_kinds(ROWS, COLS)
_ev = int(np.flatnonzero(_kinds == api.WARP_NODE_EDGE_V)[0])
_eh = int(np.flatnonzero(_kinds == api.WARP_NODE_EDGE_H)[0])
_cap_edge = api.WARP_EDGE_DISP_FRAC * cap
CASES += [
    ("edge_v_vertical_ok",   _dst({_ev: (0.0, _cap_edge * 0.5)})),
    ("edge_v_horizontal_bad", _dst({_ev: (_cap_edge * 0.5, 0.0)})),
    ("edge_h_horizontal_ok", _dst({_eh: (_cap_edge * 0.5, 0.0)})),
    ("edge_h_vertical_bad",  _dst({_eh: (0.0, _cap_edge * 0.5)})),
    ("edge_over_its_cap",    _dst({_ev: (0.0, _cap_edge * 1.3)})),
    ("corner_moved",         _dst({0: (60.0, 60.0)})),
]

# a non-trivial global affine, so the composition order (affine THEN warp) is
# actually exercised rather than cancelling out
AFF = dict(cx=(EXTENT[0] + EXTENT[1]) / 2, cy=(EXTENT[2] + EXTENT[3]) / 2,
           tx=120.0, ty=-80.0, rot_deg=4.0, sx=1.05, sy=0.97)

payload = {"extent": EXTENT,
           "cases": [{"dst": d.tolist(), "pts": PTS.tolist(), "affine": AFF}
                     for _, d in CASES]}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump(payload, fh)
    payload_path = fh.name

proc = subprocess.run(
    ["node", os.path.join("validation", "_grid_check.mjs"), payload_path],
    cwd=STUDY, capture_output=True, text=True,
)
if proc.returncode != 0:
    print(proc.stderr)
    raise SystemExit("node grid harness failed")
js = json.loads(proc.stdout)
os.unlink(payload_path)

# ---- 1-3: lattice, radius, fixed nodes ------------------------------------
src_js = np.asarray(js["nodes"], float)
dN = float(np.abs(src_py - src_js).max())
check("lattice parity (gridNodes == warp_grid_nodes)", dN < TOL,
      f"{src_py.shape[0]} nodes, |d|={dN:.2e} µm")
dR = abs(R_py - float(js["R"]))
check("radius parity (gridRadius == warp_grid_radius)", dR < TOL,
      f"R={R_py:.2f} µm, |dR|={dR:.2e}")
edge_py = api.warp_grid_edge_mask()
edge_js = np.asarray(js["edge"], bool)
check("border mask parity", bool(np.array_equal(edge_py, edge_js)),
      f"{int(edge_py.sum())} border / {edge_py.size}")
kinds_py = api.warp_node_kinds()
kinds_js = np.asarray(js["kinds"], int)
check("node-kind parity (corner / edge-V / edge-H / free)",
      bool(np.array_equal(kinds_py, kinds_js)),
      f"{int((kinds_py == api.WARP_NODE_CORNER).sum())} fixed, "
      f"{int((kinds_py == api.WARP_NODE_EDGE_V).sum())} vertical-only, "
      f"{int((kinds_py == api.WARP_NODE_EDGE_H).sum())} horizontal-only, "
      f"{int((kinds_py == api.WARP_NODE_FREE).sum())} free")
check("movable count parity", js["consts"]["movable"] == api.warp_movable_count(),
      f"{api.warp_movable_count()} movable")

# ---- 4: shared constants --------------------------------------------------
c = js["consts"]
consts_ok = (
    c["algo"] == api.WARP_GRID_ALGO and c["rows"] == ROWS and c["cols"] == COLS and
    abs(c["radius_factor"] - api.WARP_GRID_RADIUS_FACTOR) < 1e-12 and
    abs(c["max_disp_frac"] - api.WARP_MAX_DISP_FRAC) < 1e-12 and
    abs(c["max_disp_frac_ceiling"] - api.WARP_MAX_DISP_FRAC_CEILING) < 1e-12 and
    abs(c["max_disp_default"]
        - api.warp_max_disp(EXTENT, ROWS, COLS, None)) < 1e-9 and
    abs(c["max_disp_ceiling"]
        - api.warp_max_disp(EXTENT, ROWS, COLS,
                            api.WARP_MAX_DISP_FRAC_CEILING)) < 1e-9 and
    # an over-large request must clamp to the ceiling identically on both sides
    abs(c["max_disp_clamped"]
        - api.warp_max_disp(EXTENT, ROWS, COLS, 999.0)) < 1e-9 and
    abs(c["jac_min_boosted"] - api.WARP_JAC_MIN_BOOSTED) < 1e-12 and
    abs(c["jac_min_default"] - api.warp_jac_min(None)) < 1e-12 and
    abs(c["jac_min_raised"]
        - api.warp_jac_min(api.WARP_MAX_DISP_FRAC_CEILING)) < 1e-12 and
    abs(c["jac_min"] - api.WARP_JAC_MIN) < 1e-12 and
    abs(c["jac_max"] - api.WARP_JAC_MAX) < 1e-12 and
    c["jac_samples"] == api.WARP_JAC_SAMPLES and
    c["jac_per_cell"] == api.WARP_JAC_PER_CELL and
    c["jac_n"] == api.warp_jac_samples(ROWS, COLS) and
    c["jac_n_5x5"] == api.warp_jac_samples(5, 5) and
    abs(c["edge_disp_frac"] - api.WARP_EDGE_DISP_FRAC) < 1e-12 and
    (c["node_corner"], c["node_edge_v"], c["node_edge_h"], c["node_free"]) ==
    (api.WARP_NODE_CORNER, api.WARP_NODE_EDGE_V, api.WARP_NODE_EDGE_H,
     api.WARP_NODE_FREE) and
    abs(c["jac_eps"] - api.WARP_JAC_EPS) < 1e-12)
check("constants identical (warp.js == pipeline_api WARP_*)", consts_ok,
      f"{c['algo']} {c['rows']}x{c['cols']} R_factor={c['radius_factor']} "
      f"cap={c['max_disp_frac']} J=[{c['jac_min']}, {c['jac_max']}]")

# ---- 5-6: forward field + validation verdict, per case --------------------
print("Per-case forward-field and verdict parity:")
for (name, dst), jr in zip(CASES, js["results"]):
    out_py = api.warp_csrbf_points(src_py, dst, R_py, PTS)
    out_js = np.asarray(jr["out"], float)
    dO = float(np.abs(out_py - out_js).max())
    check(f"{name}: forward CSRBF parity", dO < TOL, f"max |d|={dO:.2e} µm")

    v_py = api.validate_warp_grid(src_py, dst, R_py, EXTENT)
    v_js = jr["valid"]
    same_ok = bool(v_py["ok"]) == bool(v_js["ok"])
    # reason strings match verbatim for the structural rejections; the Jacobian
    # message embeds formatted numbers, so compare only its stable prefix.
    r_py, r_js = v_py["reason"], v_js["reason"]
    same_reason = (r_py == r_js or
                   (r_py.startswith("warp folds") and r_js.startswith("warp folds")))
    same_n = int(v_py["n_moved"]) == int(v_js["nMoved"])
    check(f"{name}: verdict parity", same_ok and same_reason and same_n,
          f"ok={v_py['ok']} n_moved={v_py['n_moved']} reason='{r_py}'")

    # the composition the CURATION CANVAS draws with: affine THEN forward CSRBF
    M = api.affine_from_params(**AFF)
    placed_py = api.warp_csrbf_points(src_py, dst, R_py,
                                      api.affine_apply(M, PTS))
    placed_js = np.asarray(jr["placed"], float)
    dP = float(np.abs(placed_py - placed_js).max())
    check(f"{name}: place() = affine->warp parity", dP < TOL,
          f"max |d|={dP:.2e} µm")

    if v_py["ok"] and v_py["min_det"] is not None and v_js["minDet"] is not None:
        dmn = abs(float(v_py["min_det"]) - float(v_js["minDet"]))
        dmx = abs(float(v_py["max_det"]) - float(v_js["maxDet"]))
        dds = abs(float(v_py["max_disp"]) - float(v_js["maxDisp"]))
        check(f"{name}: Jacobian / displacement parity",
              dmn < 1e-6 and dmx < 1e-6 and dds < TOL,
              f"det [{v_py['min_det']:.4f}, {v_py['max_det']:.4f}] "
              f"max_disp={v_py['max_disp']:.1f} µm")

# ---- the three illegal cases MUST be rejected by both sides ---------------
for name in ("over_disp_cap", "edge_node_moved", "fold",
             "edge_v_horizontal_bad", "edge_h_vertical_bad",
             "edge_over_its_cap", "corner_moved"):
    i = [n for n, _ in CASES].index(name)
    rejected = (not api.validate_warp_grid(src_py, CASES[i][1], R_py, EXTENT)["ok"]
                and not js["results"][i]["valid"]["ok"])
    check(f"{name}: rejected by BOTH sides", rejected)

# ---- preview vs final labels: F^-1(F(x)) == x ? --------------------------
# The canvas draws the FORWARD field F (centers = src); finalize resamples by
# inverting THAT field numerically at every output pixel (warp_csrbf_invert). The
# residual |F^-1(F(x)) - x| is the real geometric disagreement between what curation
# previews and where the labels land, so it must be far below one 50 µm atlas voxel.
#
# It replaces a single-shot "pull" CSRBF fitted dst->src, which was exact only AT
# the nodes and drifted to ~1 voxel between them at the largest legal displacement.
# Both are measured here so the improvement is on the record.
TARGET_P95, TARGET_MAX = 5.0, 10.0
print("Preview-vs-labels round trip (forward, then the backend's inverse):")
M = api.affine_from_params(**AFF)
aff_pts = api.affine_apply(M, PTS)
# denser probe set: the residual is largest BETWEEN control points, so sampling only
# the nodes/corners would flatter the result
_rng = np.random.default_rng(11)
PROBE = np.vstack([aff_pts,
                   np.c_[_rng.uniform(EXTENT[0], EXTENT[1], 2000),
                         _rng.uniform(EXTENT[2], EXTENT[3], 2000)]])
for name, dst in CASES:
    v = api.validate_warp_grid(src_py, dst, R_py, EXTENT)
    if not v["ok"] or v["n_moved"] == 0:
        continue
    fwd = api.warp_csrbf_points(src_py, dst, R_py, PROBE)

    back, st = api.warp_csrbf_invert(src_py, dst, R_py, fwd)
    res = np.linalg.norm(back - PROBE, axis=1)
    p95, mx = float(np.percentile(res, 95)), float(res.max())

    w_pull = api._csrbf_weights(dst, src_py - dst, R_py)
    old = fwd + api._csrbf_eval(dst, w_pull, R_py, fwd)
    ores = np.linalg.norm(old - PROBE, axis=1)
    op95, omx = float(np.percentile(ores, 95)), float(ores.max())

    check(f"{name}: round trip p95 < {TARGET_P95} µm", p95 < TARGET_P95,
          f"p95 {p95:.2e} µm  (old pull field: {op95:.2f} µm)")
    check(f"{name}: round trip max < {TARGET_MAX} µm", mx < TARGET_MAX,
          f"max {mx:.2e} µm  (old pull field: {omx:.2f} µm)")
    check(f"{name}: every probe converged", st["n_bad"] == 0,
          f"{st['iters']} Newton iters, max resid {st['max_resid']:.2e} µm")

# the inverse must also be exact AT the control points (dst_i -> src_i)
for name, dst in CASES:
    v = api.validate_warp_grid(src_py, dst, R_py, EXTENT)
    if not v["ok"] or v["n_moved"] == 0:
        continue
    back_nodes, _ = api.warp_csrbf_invert(src_py, dst, R_py, dst)
    dn = float(np.abs(back_nodes - src_py).max())
    check(f"{name}: inverse is exact AT the nodes", dn < 1e-2, f"max {dn:.2e} µm")

print()
if _fail:
    print("FAILED:", ", ".join(_fail))
    raise SystemExit(1)
print("ALL GRID PARITY CHECKS PASSED")
