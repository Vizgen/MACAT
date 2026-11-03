"""Automatic registration: rasterize MERFISH cell density, fit the Allen volume to
that plane, and write the bundle every later stage reads.

The fit is the silhouette-seeded affine below, computed with numpy only
(allen_alignment/raster.py): no torch, no GPU. Local, nonlinear correction is
done by hand in curation. (An optional STalign LDDMM path existed until
2026-10-27; it was never reachable from the app and was removed so the package
carries no GPL code.) Replacing the registration method means replacing this file; as long as it
writes a conforming manual_bundle npz, nothing downstream changes."""
import numpy as np
import pandas as pd
import os
import config
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from . import raster
from .atlas import (RES, atlas_provenance, check_bundle_resolution, get_atlas)
from . import planes as _planes
from .boundaries import (DEPTH2_FILL_ALPHA_RESULTS, _hex_to_rgb, depth2_boundaries, depth2_fill_rgba, depth2_region_labels)
from .figures import (_curated_result_fig, _strip_axes, _top_leaf_region_styles)


def validate_region(exp_path):
    """(ok, message). A usable region dir has cell_metadata.csv + a DAPI mosaic."""
    if not os.path.isdir(exp_path):
        return False, f"not a directory: {exp_path}"
    if not os.path.exists(os.path.join(exp_path, "cell_metadata.csv")):
        return False, "missing cell_metadata.csv"
    dapi = os.path.join(exp_path, "images", "mosaic_DAPI_z3.tif")
    return True, ("ok" if os.path.exists(dapi) else "ok (no DAPI; density-only AP)")



def register_region(exp_path, angle, flip, ap_idx, *, plane=None, dx=None,
                    out_dir="m3_output", tag="", atlas=None, init_A=None,
                    init="silhouette", progress=None, hemisection=None):
    """Run 3D-to-slice registration on a NEW region and write per-cell Allen labels.

    exp_path : folder with cell_metadata.csv (center_x/center_y + any cols to keep).
    angle,flip : orientation to dorsal-up (deg CCW, flip in {None,'x','y'}).
    ap_idx : human-verified Allen coronal AP index (allen_mouse_50um).
    hemisection : None for a whole section, or "left"/"right" for a coronal
        section that is one half of the brain (the half as displayed, dorsal
        up). Declared by the user; see planes.hemisection_fit.
    Returns dict: csv_path, fig_path, n_cells, n_mapped, coverage, tilt_deg,
                  top_regions [(acronym, name, count, coarse_short, coarse_color), ...].
    """
    def say(m):
        if progress:
            progress(m)

    os.makedirs(out_dir, exist_ok=True)
    atlas = atlas or get_atlas()
    provenance = atlas_provenance(atlas)

    # ---- TARGET J: oriented MERFISH density (dorsal-up) ----
    say("loading cells ...")
    meta = pd.read_csv(os.path.join(exp_path, "cell_metadata.csv"))
    xr = meta["center_x"].to_numpy(); yr = meta["center_y"].to_numpy()
    xr, yr = config.orient_apply(xr, yr, angle, flip)
    # resolved here rather than in the signature so an env override reaches a
    # worker started after this module was imported
    dx = float(config.PLANE_DX_UM if dx is None else dx)
    # Which atlas axis this section is cut along. `ap_idx` indexes THAT axis, so
    # for sagittal it is an ML index; the name is kept because it is the field
    # every job, bundle and API already carries.
    plane = _planes.normalize(plane)
    hemisection = _planes.normalize_hemisection(hemisection, plane)
    _slice_ax, _row_ax, _col_ax = _planes.axes(plane)
    X, Y, M = raster.density_raster(xr, yr, dx)
    Jimg = raster.minmax(M)                     # rows<->Y(SI), cols<->X(LR)
    drow, dcol = Y[1] - Y[0], X[1] - X[0]
    r0, c0, rsrc, csrc = Y[0], X[0], yr, xr
    xJ = [Y - Y.mean(), X - X.mean()]
    say(f"target {Jimg.shape}, {len(xr):,} cells; loading atlas ...")

    # ---- SOURCE I: 3D Allen reference ----
    ref, ann = atlas.reference, atlas.annotation
    nAP, nSI, nLR = ref.shape
    xI = _planes.axis_coords(ref.shape, RES)
    I = raster.minmax(ref)[None]
    ap_um = _planes.slice_coordinate(xI, int(ap_idx), plane)
    if init_A is not None:
        A0 = np.asarray(init_A, dtype=np.float64).copy()
    else:
        A0 = np.eye(4)
        # The atlas is sampled with the explicit FORWARD map
        # ``atlas_xyz = A0 @ plane_xyz`` below, so the slice-axis translation is
        # the selected atlas coordinate itself.
        A0[_slice_ax, 3] = ap_um
        if init == "silhouette":
            # Seed the affine SI/LR scale by matching the Allen slice tissue
            # silhouette (rendered on the initial coronal plane) to the MERFISH
            # tissue silhouette.
            def _span(mask, axis):
                idx = np.where(mask.any(axis=1 - axis))[0]
                return (idx[-1] - idx[0] + 1) if idx.size else mask.shape[axis]
            # Size initialization uses the literal user-selected slice.
            # atlas_slice returns (row, col) for THIS plane, so the two spans
            # below always mean "down the image" and "across the image" -- for
            # sagittal a raw np.take would hand back (AP, SI) and swap them.
            aimg = _planes.atlas_slice(I[0], int(ap_idx), plane)
            ma = aimg > 0.10 * (aimg.max() + 1e-9)
            mj = Jimg > 0.10 * (Jimg.max() + 1e-9)
            if hemisection:
                # Half a brain: fit against the declared half of the slice and
                # move the tissue's centre onto that half's centre (a column
                # translation in the FORWARD map's convention).
                f_row, f_col, centre = _planes.hemisection_fit(
                    ma, mj, xI[_col_ax], hemisection)
                A0[_row_ax, _row_ax] = f_row
                A0[_col_ax, _col_ax] = f_col
                A0[_col_ax, 3] = centre
                say(f"silhouette init ({plane}, {hemisection} hemisection): row scale "
                    f"{f_row:.3f}, col scale {f_col:.3f}, centre {centre:.0f} um")
            else:
                f_row = _span(ma, 0) / max(_span(mj, 0), 1)
                f_col = _span(ma, 1) / max(_span(mj, 1), 1)
                A0[_row_ax, _row_ax] = float(np.clip(f_row, 0.6, 1.6))
                A0[_col_ax, _col_ax] = float(np.clip(f_col, 0.6, 1.6))
                say(f"silhouette init ({plane}): row scale {A0[_row_ax,_row_ax]:.3f}, "
                    f"col scale {A0[_col_ax,_col_ax]:.3f}")

    # ---- registration transform ----
    # The silhouette-seeded affine A0 is the automatic result. It gives a
    # reliable GLOBAL alignment (position, scale, overall outline); local
    # landmark refinement is left to manual curation. On this data A0 was shown
    # to beat both an optimised affine and affine+nonlinear LDDMM, which
    # over-warped the cell-density<->template match (and, on the positive
    # controls, scored lower; checked 2026-10-27).
    say(f"using silhouette A0 initialization, AP={ap_idx}")
    A_np = A0
    Lin = A_np[:3, :3]
    normal = Lin[:, _slice_ax] / (np.linalg.norm(Lin[:, _slice_ax]) + 1e-12)
    tilt_deg = float(np.degrees(np.arccos(np.clip(abs(normal[_slice_ax]), 0, 1))))

    # ---- sample annotation along the fitted plane ----
    say("sampling Allen regions per cell ...")
    XJg = _planes.plane_points(xJ[0], xJ[1], plane)
    pts = XJg @ A_np[:3, :3].T + A_np[:3, 3]   # forward affine: J-plane -> atlas um
    # atlas label at each plane pixel, nearest voxel
    AphiL = raster.sample_nearest(xI, ann, pts)[0].astype(np.int32)

    row = np.round((rsrc - r0) / drow).astype(int)
    col = np.round((csrc - c0) / dcol).astype(int)
    inb = (row >= 0) & (row < AphiL.shape[0]) & (col >= 0) & (col < AphiL.shape[1])
    rid = np.zeros(len(xr), dtype=int)
    rid[inb] = AphiL[row[inb], col[inb]]

    tf = np.asarray(pts[0], dtype=np.float64)
    # cells whose sample point is past the end of the volume: unlabelled because
    # the REFERENCE stops there, which the UI reports separately from coverage
    overhang = _planes.atlas_overhang(tf, row, col, inb, ann.shape, RES)
    if overhang["n"]:
        say(f"beyond the atlas: {overhang['n']:,} cells "
            f"({overhang['frac'] * 100:.2f}%) -- " + ", ".join(
                f"{e['end']} {e['n']:,}" for e in overhang["ends"]))
    sampled_ap_um = float(np.median(tf[..., _slice_ax]))
    sampled_ap_idx = _planes.nearest_index(xI, sampled_ap_um, plane)
    say(
        "AP audit: "
        f"selected_idx={int(ap_idx)}, selected_um={float(ap_um):.1f}, "
        f"sampled_idx={sampled_ap_idx}, sampled_median_um={sampled_ap_um:.1f}"
    )
    if sampled_ap_idx != int(ap_idx):
        raise RuntimeError(
            "AP sampling mismatch before bundle write: "
            f"selected_idx={int(ap_idx)} selected_um={float(ap_um):.1f} "
            f"sampled_idx={sampled_ap_idx} sampled_um={sampled_ap_um:.1f}"
        )
    ap_c = np.full(len(xr), np.nan); si_c = np.full(len(xr), np.nan); lr_c = np.full(len(xr), np.nan)
    ap_c[inb] = tf[row[inb], col[inb], 0]
    si_c[inb] = tf[row[inb], col[inb], 1]
    lr_c[inb] = tf[row[inb], col[inb], 2]

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
    meta["allen_region_id"] = rid
    meta["allen_acronym"] = [info[u][0] for u in rid]
    meta["allen_name"] = [info[u][1] for u in rid]
    meta["allen_parent"] = [info[u][2] for u in rid]
    meta["allen_ontology_path"] = [info[u][3] for u in rid]
    meta["mapping_status"] = [info[u][4] for u in rid]
    meta["atlas_ap_um"] = ap_c; meta["atlas_si_um"] = si_c; meta["atlas_lr_um"] = lr_c
    csvout = os.path.join(out_dir, f"cell_metadata_with_allen_regions{tag}.csv")
    meta.to_csv(csvout, index=False)

    uids, counts = np.unique(rid[rid > 0], return_counts=True)
    order = np.argsort(-counts)
    top, leaf_colors = _top_leaf_region_styles(uids, counts, order, info)

    # ---- figure ----
    scales = np.linalg.norm(Lin, axis=0)            # affine scaling: [AP, SI, LR]
    # Draw the SAME sparse landmark set the manual-curation editor uses (grouped
    # to major structures + outer contour + midline), so the auto-registration
    # preview and the curation step stay visually and coordinate-consistent. The
    # full leaf-level Allen boundaries are intentionally NOT shown here (they are
    # only available as the curation "medium"/"full" debug overlays).
    _lm_bundle = {
        "AphiL": AphiL.astype(np.int32), "tf": tf.astype(np.float32),
        "xJ0": xJ[0].astype(np.float32), "xJ1": xJ[1].astype(np.float32),
        "id2acro": {int(u): info[u][0] for u in info},
        "id2name": {int(u): info[u][1] for u in info},
    }
    lm_feats = depth2_boundaries(_lm_bundle, atlas=atlas)
    # Morig is computed here rather than further down because the on-demand
    # automatic figure needs it to put the DAPI mosaic on the plane; the bundle
    # written below reuses it.
    Gm = np.c_[xr, yr, np.ones_like(xr)]
    Morig, *_ = np.linalg.lstsq(Gm, np.c_[meta["center_x"].to_numpy(float),
                                          meta["center_y"].to_numpy(float)],
                                rcond=None)
    # The automatic result figure is NOT rendered here (2026-09-22). It cost ~10 s
    # of a ~16 s registration and ~40 MB per job, on a set that is only looked at
    # when someone explicitly compares against the curated result -- and the
    # "vs automatic" coverage number in Results comes from this function's return
    # value, not from any figure, so nothing routine depends on it.
    # `render_auto_result()` below rebuilds it from the saved bundle on request,
    # the same on-demand-and-cache shape the region exports and hex QC already use.
    figs = {}
    figout = None

    # ---- save the fit in the stable format consumed by region-level QC /
    #      polygon export can reproduce the fitted plane without re-optimizing ----
    # npz (until 2026-10-27 a torch .pt): the forward affine and the grids it
    # was fitted on, readable with numpy alone (m4_expr_corr.load_fit).
    fitout = os.path.join(out_dir, f"m3_fit_3d{tag}.npz")
    np.savez_compressed(
        fitout, A=np.asarray(A_np, dtype=np.float64),
        **{f"xI{i}": np.asarray(x) for i, x in enumerate(xI)},
        xJ0=np.asarray(xJ[0]), xJ1=np.asarray(xJ[1]), ap=int(ap_idx), dx=float(dx),
        angle=float(angle), flip=str(flip), atlas=f"allen_mouse_{int(RES)}um")

    # ---- manual-curation bundle: everything needed to re-derive per-cell labels
    #      + region polygons under an in-plane 2D affine WITHOUT re-registering ----
    # Morig was computed above, before the figure that needs it.
    bundleout = os.path.join(out_dir, f"manual_bundle{tag}.npz")
    np.savez_compressed(
        bundleout, AphiL=AphiL.astype(np.int32), tf=tf.astype(np.float32),
        Jimg=Jimg.astype(np.float32),
        r0=float(r0), c0=float(c0), drow=float(drow), dcol=float(dcol),
        row=row.astype(np.int32), col=col.astype(np.int32), inb=inb,
        xr=xr.astype(np.float32), yr=yr.astype(np.float32),
        xJ0=xJ[0].astype(np.float32), xJ1=xJ[1].astype(np.float32),
        Morig=Morig.astype(np.float64), dx=float(dx), ap=int(ap_idx),
        selected_ap_um=float(ap_um), sampled_ap_idx=int(sampled_ap_idx),
        sampled_ap_um=float(sampled_ap_um), plane=str(plane),
        exp_path=str(exp_path),
        atlas_provenance=np.array(provenance, dtype=object),
        id2acro=np.array({int(u): info[u][0] for u in info}, dtype=object),
        id2name=np.array({int(u): info[u][1] for u in info}, dtype=object))

    say("done.")
    return dict(csv_path=csvout, fig_path=figout,
                figure_zip_path=figs.get("zip"), fit_path=fitout,
                bundle_path=bundleout, n_cells=int(len(rid)),
                n_mapped=int((rid > 0).sum()), coverage=float((rid > 0).mean()),
                tilt_deg=tilt_deg, top_regions=top,
                scale_ap=float(scales[0]), scale_si=float(scales[1]),
                scale_lr=float(scales[2]), selected_ap_idx=int(ap_idx),
                selected_ap_um=float(ap_um), sampled_ap_idx=int(sampled_ap_idx),
                sampled_ap_um=float(sampled_ap_um), plane=str(plane),
                atlas_overhang=overhang, hemisection=hemisection,
                atlas_provenance=provenance)



# ===================================================================== manual
# Manual curation: after the automatic fit, let a human nudge the Allen overlay
# in-plane (shift / scale / rotate) until the thin boundaries match the tissue,
# then regenerate the final per-cell labels + region polygons. An in-plane 2D
# affine on the already-sampled annotation grid is exact (it commutes with the
# coronal-plane sampling), so no re-registration is needed -- previews are
# ~instant.

def load_bundle(path):
    d = np.load(path, allow_pickle=True)
    # Every reader -- server, finalize worker, auto figure -- comes through here,
    # so this is the one place a bundle from another resolution can be stopped.
    check_bundle_resolution(d, path)
    objects = ("id2acro", "id2name", "atlas_provenance")
    b = {k: d[k] for k in d.files if k not in objects}
    b["id2acro"] = d["id2acro"].item()
    b["id2name"] = d["id2name"].item()
    if "atlas_provenance" in d.files:
        b["atlas_provenance"] = d["atlas_provenance"].item()
    return b


def render_auto_result(bundle_path, out_dir, tag, atlas=None):
    """Render the AUTOMATIC result figure from a saved bundle, on demand.

    Everything needed is already in the bundle, so this is a pure re-render with
    no re-registration: `AphiL` is the un-warped annotation the automatic fit
    produced, and the per-cell ids come straight back out of it. That is why the
    figure can be dropped from the registration path without losing anything --
    it was never carrying information the bundle does not.

    Returns the dict `_curated_result_fig` returns, or {} if it cannot be built.
    """
    b = load_bundle(bundle_path)
    A = np.asarray(b["AphiL"])
    row = np.asarray(b["row"]).astype(np.int64)
    col = np.asarray(b["col"]).astype(np.int64)
    inb = np.asarray(b["inb"]).astype(bool)
    keep = (inb & (row >= 0) & (row < A.shape[0])
            & (col >= 0) & (col < A.shape[1]))
    rid = np.zeros(len(row), dtype=int)
    rid[keep] = A[row[keep], col[keep]]

    id2acro = np.asarray(b["id2acro"]).item() if hasattr(b["id2acro"], "item") \
        else b["id2acro"]
    id2name = np.asarray(b["id2name"]).item() if hasattr(b["id2name"], "item") \
        else b["id2name"]
    uids, counts = np.unique(rid[rid > 0], return_counts=True)
    info = {int(u): (str(id2acro.get(int(u), u)), str(id2name.get(int(u), u)),
                     "", "", "") for u in uids}
    top, leaf_colors = _top_leaf_region_styles(uids, counts, np.argsort(-counts),
                                               info)
    return _curated_result_fig(
        b, A, rid, out_dir, tag, atlas or get_atlas(), top, leaf_colors,
        prefix="register",
        suptitle=f"automatic registration | {(rid > 0).mean() * 100:.1f}% labeled",
    ) or {}
