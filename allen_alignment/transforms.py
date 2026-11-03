"""Applying a transform to the label raster: the global affine, the manual
affine, and the affine+CSRBF composite. The math that decides WHERE atlas
labels land, as opposed to warp.py which decides whether a deformation is legal."""
import numpy as np
from .plane import (_plane_spacing)
from .warp import (WARP_INV_TOL, _csrbf_eval, _csrbf_weights,
                   warp_csrbf_invert, warp_csrbf_points)


def _manual_matrix(center, dsi_px, dlr_px, ssi, slr, rot_deg):
    """scipy affine_transform (matrix, offset) that visually moves the atlas by
    rotate(rot_deg) -> scale(ssi,slr) -> shift(dsi,dlr px) about the image center,
    in (row=SI, col=LR) pixel coordinates."""
    th = np.radians(rot_deg)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    Sinv = np.diag([1.0 / max(ssi, 1e-6), 1.0 / max(slr, 1e-6)])
    matrix = Sinv @ R.T                                   # S^-1 R^-1 (sampling)
    c = np.asarray(center, float)
    t = np.array([dsi_px, dlr_px], float)
    offset = c - matrix @ (c + t)
    return matrix, offset



def apply_manual(bundle, dlr_um=0.0, dsi_um=0.0, slr=1.0, ssi=1.0, rot_deg=0.0):
    """Return (AphiL_adj, tf_adj, rid) after an in-plane manual affine."""
    from scipy import ndimage
    AphiL = bundle["AphiL"]; tf = bundle["tf"]
    drow = float(bundle["drow"]); dcol = float(bundle["dcol"])
    H, W = AphiL.shape
    center = np.array([H / 2.0, W / 2.0])
    matrix, offset = _manual_matrix(center, dsi_um / drow, dlr_um / dcol,
                                    ssi, slr, rot_deg)
    A2 = ndimage.affine_transform(AphiL.astype(np.float64), matrix, offset=offset,
                                  order=0, mode="constant", cval=0.0,
                                  output_shape=AphiL.shape).astype(np.int32)
    tf2 = np.stack([ndimage.affine_transform(tf[..., k], matrix, offset=offset,
                                             order=1, mode="nearest",
                                             output_shape=AphiL.shape)
                    for k in range(3)], -1)
    row = bundle["row"]; col = bundle["col"]; inb = bundle["inb"]
    rid = np.zeros(len(row), dtype=int)
    ib = inb & (row >= 0) & (row < H) & (col >= 0) & (col < W)
    rid[ib] = A2[row[ib], col[ib]]
    return A2, tf2, rid



# ================================================== global in-plane affine (Phase 1)
# Canonical 2D affine for manual curation. Parameterised by a translation, a
# rotation and independent x/y scales about a FIXED centre (the atlas bbox centre
# in plane microns). Composition order is scale -> rotate -> translate, all about
# the centre. This exact formula is mirrored in web/frontend/src/affine.js so the
# browser preview and the backend finalize apply identical math. No shear, no
# perspective; det = sx*sy > 0 (scales are clamped positive) so no fold/reflection.
AFFINE_SCALE_MIN = 0.2

AFFINE_SCALE_MAX = 5.0



def affine_from_params(cx, cy, tx, ty, rot_deg, sx, sy):
    """Forward affine (2x3, plane microns [lr, si]) from curation parameters.
    forward(p) = A @ p + b with A = R(rot) @ diag(sx, sy) about centre (cx, cy)."""
    sx = float(np.clip(sx, AFFINE_SCALE_MIN, AFFINE_SCALE_MAX))
    sy = float(np.clip(sy, AFFINE_SCALE_MIN, AFFINE_SCALE_MAX))
    th = np.radians(float(rot_deg))
    ct, st = np.cos(th), np.sin(th)
    A = np.array([[ct, -st], [st, ct]], float) @ np.diag([sx, sy])
    c = np.array([float(cx), float(cy)])
    t = np.array([float(tx), float(ty)])
    b = c + t - A @ c
    return np.array([[A[0, 0], A[0, 1], b[0]],
                     [A[1, 0], A[1, 1], b[1]]], float)



def affine_apply(M, pts):
    """Apply a 2x3 affine to [N,2] plane-micron points."""
    p = np.asarray(pts, float).reshape(-1, 2)
    A = np.asarray(M, float)[:, :2]; b = np.asarray(M, float)[:, 2]
    return p @ A.T + b



def affine_invert(M):
    """Inverse of a 2x3 affine as a 2x3 affine."""
    M = np.asarray(M, float)
    A = M[:, :2]; b = M[:, 2]
    Ai = np.linalg.inv(A)
    return np.hstack([Ai, (-Ai @ b).reshape(2, 1)])



def warp_affine(bundle, M):
    """Global affine warp of the annotation grid. ``M`` is the FORWARD 2x3 affine
    (atlas microns -> displayed microns). For each output pixel (displayed space)
    we sample the atlas label at M^{-1}(displayed µm) -- so the resampled raster
    matches the forward-transformed overlay exactly. Returns (A2, tf2, rid).
    Mirrors warp_csrbf: nearest-neighbour for labels, linear for the tf field."""
    from scipy.ndimage import map_coordinates
    AphiL = bundle["AphiL"]; tf = bundle["tf"]; H, W = AphiL.shape
    xJ0, xJ1, dSI, dLR = _plane_spacing(bundle)
    Minv = affine_invert(M)
    rr, cc = np.mgrid[0:H, 0:W]
    lr = xJ1[0] + cc.ravel() * dLR
    si = xJ0[0] + rr.ravel() * dSI
    src_um = affine_apply(Minv, np.c_[lr, si])       # displayed -> atlas microns
    sc = (src_um[:, 0] - xJ1[0]) / dLR
    sr = (src_um[:, 1] - xJ0[0]) / dSI
    sr = sr.reshape(H, W); sc = sc.reshape(H, W)
    A2 = map_coordinates(AphiL.astype(np.float64), [sr, sc], order=0,
                         mode="constant", cval=0).astype(np.int32)
    tf2 = np.stack([map_coordinates(tf[..., k], [sr, sc], order=1,
                                    mode="nearest") for k in range(3)],
                   -1).astype(np.float32)
    row = bundle["row"]; col = bundle["col"]; inb = bundle["inb"]
    rid = np.zeros(len(row), int)
    ib = inb & (row >= 0) & (row < H) & (col >= 0) & (col < W)
    rid[ib] = A2[row[ib], col[ib]]
    return A2, tf2, rid



def warp_csrbf(bundle, src_pts_um, dst_pts_um, R):
    """Local (Wendland CSRBF) warp of the annotation grid. src/dst are anchor
    positions in plane microns [lr, si]; R is the influence radius (microns).
    Returns (A2, tf2, rid). Uses the PULL field (centers=dst) so each output pixel
    samples the correct source atlas label; identical to the forward preview at
    anchors, and exactly identity beyond R of every anchor."""
    from scipy.ndimage import map_coordinates
    AphiL = bundle["AphiL"]; tf = bundle["tf"]; H, W = AphiL.shape
    xJ0, xJ1, dSI, dLR = _plane_spacing(bundle)
    src = np.asarray(src_pts_um, float).reshape(-1, 2)
    dst = np.asarray(dst_pts_um, float).reshape(-1, 2)
    if len(src) < 1 or R <= 0 or np.allclose(src, dst):
        A2 = AphiL.astype(np.int32); tf2 = tf.astype(np.float32)
    else:
        rr, cc = np.mgrid[0:H, 0:W]
        lr = xJ1[0] + cc.ravel() * dLR
        si = xJ0[0] + rr.ravel() * dSI
        out_um = np.c_[lr, si]                       # [N,2] as [lr, si]
        w = _csrbf_weights(dst, src - dst, R)        # pull: u(dst)=src-dst
        src_um = out_um + _csrbf_eval(dst, w, R, out_um)
        sc = (src_um[:, 0] - xJ1[0]) / dLR
        sr = (src_um[:, 1] - xJ0[0]) / dSI
        sr = sr.reshape(H, W); sc = sc.reshape(H, W)
        A2 = map_coordinates(AphiL.astype(np.float64), [sr, sc], order=0,
                             mode="constant", cval=0).astype(np.int32)
        tf2 = np.stack([map_coordinates(tf[..., k], [sr, sc], order=1,
                                        mode="nearest") for k in range(3)],
                       -1).astype(np.float32)
    row = bundle["row"]; col = bundle["col"]; inb = bundle["inb"]
    rid = np.zeros(len(row), int)
    ib = inb & (row >= 0) & (row < H) & (col >= 0) & (col < W)
    rid[ib] = A2[row[ib], col[ib]]
    return A2, tf2, rid



def warp_chain_points(layers, pts_um):
    """Forward the whole stack, in list order."""
    out = np.asarray(pts_um, float).reshape(-1, 2)
    for ly in layers:
        out = warp_csrbf_points(np.asarray(ly["src"], float).reshape(-1, 2),
                                np.asarray(ly["dst"], float).reshape(-1, 2),
                                float(ly["R"]), out)
    return out


def warp_composed_jac(layers, extent, samples=41, eps=1.0):
    """min/max determinant of the COMPOSED forward map, sampled over the plane.

    Validating each layer on its own is not enough: determinants multiply, so a
    stack of individually legal layers can still compose into a fold. This is the
    gate that sees the stack as one map.
    """
    if not layers:
        return 1.0, 1.0
    lr0, lr1, si0, si1 = extent
    gx, gy = np.meshgrid(np.linspace(lr0 + eps, lr1 - eps, samples),
                         np.linspace(si0 + eps, si1 - eps, samples))
    P = np.c_[gx.ravel(), gy.ravel()]
    fx = warp_chain_points(layers, P + [eps, 0])
    mx = warp_chain_points(layers, P - [eps, 0])
    fy = warp_chain_points(layers, P + [0, eps])
    my = warp_chain_points(layers, P - [0, eps])
    a = (fx[:, 0] - mx[:, 0]) / (2 * eps); bb = (fy[:, 0] - my[:, 0]) / (2 * eps)
    c = (fx[:, 1] - mx[:, 1]) / (2 * eps); d = (fy[:, 1] - my[:, 1]) / (2 * eps)
    det = a * d - bb * c
    return float(np.nanmin(det)), float(np.nanmax(det))


def _invert_chain(layers, pts_um):
    """Undo a stack of forward CSRBF layers, LAST APPLIED FIRST.

    The forward chain is F1 then F2 ... then Fn, so the inverse is
    F1^-1(F2^-1(...Fn^-1(x))) -- reverse order. Getting the order wrong produces
    a picture that still looks like a warp, just the wrong one, which is why
    tests/test_warp_grid.py pins it against a round trip rather than by eye.

    Each layer is inverted on its own. That is the point of layering: Newton
    only ever faces one gentle field, instead of a single extreme one whose
    basin of convergence has closed up. Stats are aggregated so a caller sees
    the worst link, not an average that hides it.
    """
    out = pts_um
    # `tol` is carried through because callers report it beside the residuals;
    # an aggregate that silently dropped a key the single-layer stats had would
    # only surface as a KeyError inside the finalize worker, after the confirm.
    agg = {"iters": 0, "max_resid": 0.0, "p95_resid": 0.0, "n_bad": 0,
           "tol": WARP_INV_TOL, "layers": len(layers)}
    for ly in reversed(layers):
        src = np.asarray(ly["src"], float).reshape(-1, 2)
        dst = np.asarray(ly["dst"], float).reshape(-1, 2)
        out, st = warp_csrbf_invert(src, dst, float(ly["R"]), out)
        agg["iters"] = max(agg["iters"], st["iters"])
        agg["max_resid"] = max(agg["max_resid"], st["max_resid"])
        agg["p95_resid"] = max(agg["p95_resid"], st["p95_resid"])
        agg["n_bad"] += st["n_bad"]
        agg["tol"] = st.get("tol", agg["tol"])
    return out, agg


def warp_composite_layers(bundle, M, layers):
    """Global affine ``M`` (FORWARD, atlas->displayed) THEN a STACK of local
    Wendland CSRBF layers (each src->dst forward, in DISPLAYED microns), applied
    in list order.

    The forward chain the browser previews is

        x_auto --M--> x_aff --F1--> ... --Fn--> x_final

    so resampling inverts it per OUTPUT pixel: x_aff = F1^-1(...Fn^-1(x_final)),
    each layer solved NUMERICALLY in REVERSE order (warp_csrbf_invert -- Newton on
    the true forward field, not a second approximating RBF), then
    x_auto = Minv(x_aff), then nearest-neighbour sampling of the full LEAF-level
    annotation.

    Returns (A2, tf2, rid, inv_stats); nearest-neighbour for labels, linear for the
    tf field. With dst == src (or no grid) the inverse is the identity and this is
    EXACTLY warp_affine(bundle, M)."""
    from scipy.ndimage import map_coordinates
    AphiL = bundle["AphiL"]; tf = bundle["tf"]; H, W = AphiL.shape
    xJ0, xJ1, dSI, dLR = _plane_spacing(bundle)
    Minv = affine_invert(M)
    rr, cc = np.mgrid[0:H, 0:W]
    lr = xJ1[0] + cc.ravel() * dLR
    si = xJ0[0] + rr.ravel() * dSI
    disp_um = np.c_[lr, si]                               # OUTPUT pixels (disp µm)
    aff_um, inv_stats = _invert_chain(layers, disp_um)
    src_um = affine_apply(Minv, aff_um)                  # aff -> atlas microns
    sc = (src_um[:, 0] - xJ1[0]) / dLR
    sr = (src_um[:, 1] - xJ0[0]) / dSI
    sr = sr.reshape(H, W); sc = sc.reshape(H, W)
    A2 = map_coordinates(AphiL.astype(np.float64), [sr, sc], order=0,
                         mode="constant", cval=0).astype(np.int32)
    tf2 = np.stack([map_coordinates(tf[..., k], [sr, sc], order=1,
                                    mode="nearest") for k in range(3)],
                   -1).astype(np.float32)
    row = bundle["row"]; col = bundle["col"]; inb = bundle["inb"]
    rid = np.zeros(len(row), int)
    ib = inb & (row >= 0) & (row < H) & (col >= 0) & (col < W)
    rid[ib] = A2[row[ib], col[ib]]
    return A2, tf2, rid, inv_stats


def warp_composite(bundle, M, src_pts_um, dst_pts_um, R):
    """One-layer form, kept because it is the whole of the historical API and is
    what the regression tests drive."""
    return warp_composite_layers(
        bundle, M, [{"src": src_pts_um, "dst": dst_pts_um, "R": R}])
