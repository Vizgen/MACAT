"""Finalize: apply the confirmed transform, assign leaf-level Allen labels to every
cell, and write the deliverables (per-cell CSV, region GeoJSON, result figure).
The end of the pipeline -- everything here produces files a user takes away."""
import numpy as np
import pandas as pd
import os
from .atlas import (bundle_res_um, get_atlas)
from . import planes as _planes
from .plane import closed_contours
from .warp import (WARP_COMPOSED_JAC_MIN, WARP_GRID_ALGO, WARP_GRID_COLS,
                   WARP_GRID_ROWS, WARP_INV_MAX_BAD_FRAC, WARP_INV_TOL,
                   WARP_MAX_DISP_FRAC_CEILING, WARP_MAX_LAYERS,
                   validate_warp_grid, warp_grid_extent, warp_grid_radius,
                   warp_jac_min, warp_layers, warp_max_disp)
from .transforms import (affine_from_params, warp_affine, warp_composite,
                         warp_composite_layers, warp_composed_jac, warp_csrbf)
from .figures import (_curated_result_fig, _top_leaf_region_styles)


def _bundle_geojson(bundle, A2, rid, atlas=None):
    """Region polygons (GeoJSON FeatureCollection) in the ORIGINAL MERFISH micron
    frame, built from a (possibly manually adjusted) annotation grid A2."""
    from skimage import measure
    MAX_PTS = 250
    r0 = float(bundle["r0"]); c0 = float(bundle["c0"])
    drow = float(bundle["drow"]); dcol = float(bundle["dcol"])
    M = bundle["Morig"]; id2acro = bundle["id2acro"]; id2name = bundle["id2name"]
    u, c = np.unique(rid[rid > 0], return_counts=True)
    counts = {int(a): int(b) for a, b in zip(u, c)}

    def px_to_orig(rows, cols):
        gy = r0 + rows * drow; gx = c0 + cols * dcol
        p = np.c_[gx, gy, np.ones_like(gx)] @ M
        return p[:, 0], p[:, 1]

    feats = []
    for u in np.unique(A2):
        if u <= 0:
            continue
        ac = id2acro.get(int(u))
        if not ac:
            continue
        polys = []
        for ct in closed_contours(A2 == u):
            if len(ct) < 4:
                continue
            if len(ct) > MAX_PTS:
                ct = ct[:: int(np.ceil(len(ct) / MAX_PTS))]
            X, Y = px_to_orig(ct[:, 0], ct[:, 1])
            ring = [[float(x), float(y)] for x, y in zip(X, Y)]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            polys.append([ring])
        if polys:
            feats.append({"type": "Feature",
                          "properties": {"region_acronym": ac,
                                         "region_name": id2name.get(int(u), ac),
                                         "region_id": int(u),
                                         "cell_count": counts.get(int(u), 0)},
                          "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    return {"type": "FeatureCollection", "features": feats}



def finalize_manual(bundle, exp_path, A2, tf2, rid, out_dir, tag="",
                    atlas=None):
    """Write the FINAL per-cell labels CSV + region-polygon GeoJSON from a manually
    curated annotation grid. Returns dict(csv_path, geojson_path, coverage,
    n_mapped, fig_path, top_regions [(acronym, name, count, coarse_short, coarse_color), ...])."""
    import json
    os.makedirs(out_dir, exist_ok=True)
    atlas = atlas or get_atlas()
    id2acro = bundle["id2acro"]; id2name = bundle["id2name"]

    def ontology(u):
        if u <= 0:
            return ("", "", "", "", "unmapped")
        try:
            s = atlas.structures[int(u)]
            pids = s["structure_id_path"]
            path = ">".join(atlas.structures[int(p)]["acronym"] for p in pids)
            parent = atlas.structures[int(pids[-2])]["acronym"] if len(pids) >= 2 else ""
            return (s["acronym"], s["name"], parent, path, "mapped")
        except Exception:
            return (str(u), "", "", "", "id_not_in_tree")

    info = {u: ontology(u) for u in np.unique(rid)}
    row = bundle["row"]; col = bundle["col"]; inb = bundle["inb"]
    H, W = A2.shape
    ib = inb & (row >= 0) & (row < H) & (col >= 0) & (col < W)
    ap_c = np.full(len(row), np.nan); si_c = np.full(len(row), np.nan)
    lr_c = np.full(len(row), np.nan)
    ap_c[ib] = tf2[row[ib], col[ib], 0]; si_c[ib] = tf2[row[ib], col[ib], 1]
    lr_c[ib] = tf2[row[ib], col[ib], 2]

    meta = pd.read_csv(os.path.join(exp_path, "cell_metadata.csv"))
    meta["allen_region_id"] = rid
    meta["allen_acronym"] = [info[u][0] for u in rid]
    meta["allen_name"] = [info[u][1] for u in rid]
    meta["allen_parent"] = [info[u][2] for u in rid]
    meta["allen_ontology_path"] = [info[u][3] for u in rid]
    meta["mapping_status"] = [info[u][4] for u in rid]
    meta["atlas_ap_um"] = ap_c; meta["atlas_si_um"] = si_c; meta["atlas_lr_um"] = lr_c
    # `tag` ALREADY ends in "_curated" (web/jobs.py builds it as job tag +
    # "_curated"), so appending it again here produced
    # "..._curated_curated.csv". Fixed 2026-09-22; files written before that
    # keep their doubled name and are still reachable, because a job records the
    # absolute path it wrote rather than rebuilding it.
    csvout = os.path.join(out_dir, f"cell_metadata_with_allen_regions{tag}.csv")
    meta.to_csv(csvout, index=False)

    gj = _bundle_geojson(bundle, A2, rid, atlas=atlas)
    gjout = os.path.join(out_dir, f"mapped_region_polygons{tag}.geojson")
    with open(gjout, "w") as f:
        json.dump(gj, f)

    uids, counts = np.unique(rid[rid > 0], return_counts=True)
    order = np.argsort(-counts)
    top, leaf_colors = _top_leaf_region_styles(uids, counts, order, info)

    # FINAL curated two-panel figure (same visual language as the auto preview,
    # but built from the confirmed manual warp + labels) so Results can visually
    # confirm the curated result and toggle Auto vs Curated.
    # `_curated_result_fig` now also writes each panel on its own and zips the
    # set, so it returns a dict. Still wrapped: a figure that cannot be drawn
    # must not fail a finalize that has already written the CSV and GeoJSON.
    figs = {}
    try:
        figs = _curated_result_fig(
            bundle, A2, rid, out_dir, tag, atlas, top, leaf_colors) or {}
    except Exception:
        figs = {}
    # measured on the CURATED coordinates: moving the atlas in-plane can push
    # tissue off the end of the volume, or pull it back on
    overhang = _planes.atlas_overhang(tf2, row, col, inb, tuple(atlas.shape),
                                      bundle_res_um(bundle))
    return dict(csv_path=csvout, geojson_path=gjout,
                coverage=float((rid > 0).mean()),
                n_mapped=int((rid > 0).sum()), n_total=int(len(rid)),
                fig_path=figs.get("png"), figure_zip_path=figs.get("zip"),
                top_regions=top, atlas_overhang=overhang)



def finalize_csrbf(bundle, exp_path, src_pts_um, dst_pts_um, R, out_dir, tag="",
                   atlas=None):
    """Local-warp equivalent of finalize_warp: curated per-cell CSV + region
    GeoJSON from a confirmed Wendland CSRBF boundary edit."""
    A2, tf2, rid = warp_csrbf(bundle, src_pts_um, dst_pts_um, R)
    return finalize_manual(bundle, exp_path, A2, tf2, rid, out_dir, tag=tag,
                           atlas=atlas)



def finalize_composite(bundle, exp_path, affine, warp, out_dir, tag="",
                       atlas=None):
    """Composite finalize: FORWARD affine from ``affine`` params, then the OPTIONAL
    sparse CSRBF warp grid, resample the LEAF-level annotation (nearest-neighbour)
    and write the curated per-cell CSV + region GeoJSON. The warp is RE-VALIDATED
    here (the backend never trusts the client). ``affine`` = {cx,cy,tx,ty,rot_deg,
    sx,sy}; ``warp`` = None or {algo, rows, cols, R, src:[[lr,si]..],
    dst:[[lr,si]..], disp_frac}. ``disp_frac`` is optional: omitted, the node
    displacement cap is WARP_MAX_DISP_FRAC as it has always been; supplied, it is
    clamped to WARP_MAX_DISP_FRAC_CEILING (see warp_max_disp). Returns the
    finalize_manual dict augmented with a ``warp`` stats block {applied, rows,
    cols, R, n_moved, max_disp, min_det, max_det, disp_frac,
    inv_iters, inv_max_resid, inv_p95_resid, inv_n_bad, inv_tol} -- the ``inv_*``
    fields report how well the numerical inverse of the forward field converged,
    i.e. how closely the confirmed labels match what curation previewed."""
    a = dict(affine)
    M = affine_from_params(a["cx"], a["cy"], a.get("tx", 0.0), a.get("ty", 0.0),
                           a.get("rot_deg", 0.0), a.get("sx", 1.0), a.get("sy", 1.0))
    stats = {"applied": False, "algo": WARP_GRID_ALGO, "layers": 0,
             "composed_min_det": 1.0, "composed_max_det": 1.0,
             "rows": WARP_GRID_ROWS, "cols": WARP_GRID_COLS,
             "R": None, "n_moved": 0, "max_disp": 0.0,
             "min_det": 1.0, "max_det": 1.0, "disp_frac": None,
             "inv_iters": 0, "inv_max_resid": 0.0, "inv_p95_resid": 0.0,
             "inv_n_bad": 0, "inv_tol": WARP_INV_TOL}
    layers = warp_layers(warp)
    if layers:
        if len(layers) > WARP_MAX_LAYERS:
            raise ValueError(
                f"warp has {len(layers)} layers, more than the {WARP_MAX_LAYERS} "
                f"this pipeline accepts")
        ext = warp_grid_extent(bundle)
        norm, verdicts = [], []
        for n, ly in enumerate(layers):
            rows = int(ly.get("rows", WARP_GRID_ROWS))
            cols = int(ly.get("cols", WARP_GRID_COLS))
            R = float(ly.get("R") or warp_grid_radius(bundle, rows, cols))
            src = np.asarray(ly["src"], float).reshape(-1, 2)
            dst = np.asarray(ly["dst"], float).reshape(-1, 2)
            # Honour each layer's own displacement budget, clamped to the
            # ceiling. A layer without disp_frac gets the historical cap.
            disp_frac = ly.get("disp_frac")
            v = validate_warp_grid(src, dst, R, ext, rows, cols,
                                   max_disp=warp_max_disp(ext, rows, cols, disp_frac),
                                   jac_min=warp_jac_min(disp_frac))
            if not v["ok"]:
                where = "" if len(layers) == 1 else f" (layer {n + 1} of {len(layers)})"
                raise ValueError(f"warp grid rejected by backend{where}: {v['reason']}")
            norm.append({"src": src, "dst": dst, "R": R})
            verdicts.append((rows, cols, R, disp_frac, v))
        # Per-layer verdicts are not sufficient: determinants MULTIPLY, so a
        # stack of individually legal layers can still compose into a fold. This
        # is the only gate that sees the stack as one map.
        cmin, cmax = warp_composed_jac(norm, ext)
        if cmin <= WARP_COMPOSED_JAC_MIN:
            raise ValueError(
                f"the {len(layers)} warp layers are each valid but compose to "
                f"min |J| {cmin:.3f}, at or below the {WARP_COMPOSED_JAC_MIN} "
                f"floor; remove a layer or reduce one")
        rows, cols, R, disp_frac, v = verdicts[-1]
        A2, tf2, rid, inv = warp_composite_layers(bundle, M, norm)
        # A handful of stuck pixels keep their best iterate, but a systematic
        # failure to invert would mean the labels no longer match the preview
        # anywhere near those pixels -- refuse rather than ship a silent mismatch.
        bad_frac = inv["n_bad"] / max(1, A2.size)
        if bad_frac > WARP_INV_MAX_BAD_FRAC:
            raise ValueError(
                f"warp inverse failed to converge at {inv['n_bad']} of {A2.size} "
                f"pixels ({bad_frac:.2%} > {WARP_INV_MAX_BAD_FRAC:.2%}); "
                f"max residual {inv['max_resid']:.3f} um after {inv['iters']} "
                f"iterations")
        stats.update(applied=any(w[4]["n_moved"] for w in verdicts),
                     layers=len(layers),
                     composed_min_det=cmin, composed_max_det=cmax,
                     rows=rows, cols=cols, R=R,
                     n_moved=v["n_moved"], max_disp=v["max_disp"],
                     min_det=v["min_det"], max_det=v["max_det"],
                     disp_frac=(None if disp_frac is None
                                else min(float(disp_frac),
                                         WARP_MAX_DISP_FRAC_CEILING)),
                     inv_iters=inv["iters"], inv_max_resid=inv["max_resid"],
                     inv_p95_resid=inv["p95_resid"], inv_n_bad=inv["n_bad"],
                     inv_tol=inv["tol"])
    else:
        A2, tf2, rid = warp_affine(bundle, M)
    res = finalize_manual(bundle, exp_path, A2, tf2, rid, out_dir, tag=tag,
                          atlas=atlas)
    res["warp"] = stats
    return res
