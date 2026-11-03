#!/usr/bin/env python
"""
Step-preview image renderers for the React frontend (orientation + AP steps).

These are thin rasterizers ported from the Streamlit app's cached helpers — they
do NOT touch the registration algorithm. They turn a region folder + AP index
into PNG bytes the browser can show:

  - render_base_png   : un-rotated low-res DAPI (or cell-density fallback); the
                        browser applies the live rotate/flip via a CSS transform,
  - render_atlas_png  : the Allen CCF coronal slice with HPF (red) / VS (cyan).
"""
import functools
import io
import os

import numpy as np
from PIL import Image

import config
import pipeline_api as api
from allen_alignment.atlas_utils import region_id_set

RES = int(config.ATLAS_RES_UM)

_dapi_cache = {}     # exp_path -> normalized base DAPI (float32) or None
_mask_cache = {}     # "HPF"/"VS" -> set of Allen ids
_dapi_strided_cache = {}   # (exp_path, long) -> (img, stride, mosaic shape) or None
_dapi_plane_cache = {}     # (bundle path, fingerprint, super) -> PNG bytes


def _region_ids(acronym):
    if acronym not in _mask_cache:
        _mask_cache[acronym] = region_id_set(api.get_atlas(), acronym)
    return _mask_cache[acronym]


_TO_PNG_MAX_PX = 1100


def _to_png(arr01, max_px=_TO_PNG_MAX_PX):
    """float [0,1] HxW or HxWx3 -> PNG bytes (uint8), downscaled so the longest
    side is <= max_px. Uses LANCZOS so downscaled edges stay smooth (the client
    rotates this image, so any pixelation would be very visible)."""
    a = np.clip(np.asarray(arr01, dtype=np.float32), 0, 1)
    im = Image.fromarray((a * 255).astype(np.uint8))
    if max_px and max(im.size) > max_px:
        im.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _dapi_stretch(small):
    """Percentile stretch + CLAHE + gamma for DAPI display. Shared so the
    orientation/AP previews and the curation background look identical."""
    from skimage import exposure
    p1, p99 = np.percentile(small, [1, 99.5])
    small = np.clip((small - p1) / (p99 - p1 + 1e-6), 0, 1)
    img = exposure.equalize_adapthist(small, clip_limit=0.03)
    return np.power(img, 0.7).astype(np.float32)


def _load_dapi_base(exp_path, target_long=2000):
    """Load the DAPI mosaic once at ~target_long px on the long side (enough
    native resolution that the orientation preview never has to be upscaled).
    Strided read keeps memory bounded; final trim + normalization stay float."""
    if exp_path in _dapi_cache:
        return _dapi_cache[exp_path]
    path = os.path.join(exp_path, "images", "mosaic_DAPI_z3.tif")
    base = None
    if os.path.exists(path):
        import tifffile
        mm = tifffile.memmap(path, mode="r")
        h, w = mm.shape[:2]
        ds = max(1, int(max(h, w) // target_long))
        small = np.ascontiguousarray(mm[::ds])[:, ::ds].astype(np.float32)
        base = _dapi_stretch(small)
    _dapi_cache[exp_path] = base
    return base


def dapi_path(exp_path):
    return os.path.join(exp_path, "images", "mosaic_DAPI_z3.tif")


def micron_to_mosaic_path(exp_path):
    return os.path.join(exp_path, "images", "micron_to_mosaic_pixel_transform.csv")


def has_dapi(exp_path):
    """Can a plane-aligned DAPI background be produced for this experiment? Needs
    BOTH the mosaic and the micron->pixel transform (the transform is what puts the
    mosaic in the same frame as the cell coordinates)."""
    return (bool(exp_path) and os.path.exists(dapi_path(exp_path))
            and os.path.exists(micron_to_mosaic_path(exp_path)))


def _dapi_strided(exp_path, target_long=4000):
    """DAPI mosaic read with a stride, returned WITH the stride and the full mosaic
    shape so mosaic-pixel coordinates can be mapped into the strided array. A
    coarser read than the native mosaic keeps memory bounded; target_long is well
    above the plane grid so the resample below is a downsample, not an upsample."""
    key = (exp_path, target_long)
    if key in _dapi_strided_cache:
        return _dapi_strided_cache[key]
    out = None
    path = dapi_path(exp_path)
    if os.path.exists(path):
        import tifffile
        mm = tifffile.memmap(path, mode="r")
        h, w = mm.shape[:2]
        ds = max(1, int(max(h, w) // target_long))
        small = np.ascontiguousarray(mm[::ds])[:, ::ds].astype(np.float32)
        out = (_dapi_stretch(small), ds, (h, w))
    _dapi_strided_cache[key] = out
    return out


def dapi_on_plane(bundle, supersample=3):
    """DAPI resampled onto the bundle's PLANE grid, so it can be swapped in for the
    cell-density background under the very same atlas overlay.

    The coordinate chain (all of it recovered from the bundle, no re-registration):

        plane micron [lr, si]      the frame the atlas overlay lives in
          + (X.mean(), Y.mean())   -> ORIENTED micron  (xJ1 = X - X.mean())
          @ Morig                  -> ORIGINAL micron  (the least-squares
                                      oriented->original fit register_region saved)
          @ micron_to_mosaic       -> DAPI mosaic pixel

    Sample positions are the pixel centres of an edge-to-edge draw over
    [lr0, lr1] x [si0, si1] -- i.e. exactly how the client already draws the density
    background -- so toggling between the two backgrounds cannot shift the image.
    `supersample` only raises the output resolution (DAPI has real structure worth
    resolving, unlike the smooth density map); it does not change the extent.

    Returns float32 [0, 1] of shape (H*s, W*s), or None if this experiment has no
    DAPI mosaic / transform.
    """
    exp_path = str(bundle["exp_path"]) if "exp_path" in bundle else ""
    if not has_dapi(exp_path):
        return None
    st = _dapi_strided(exp_path)
    if st is None:
        return None
    small, ds, _shape = st

    from scipy.ndimage import map_coordinates
    H, W = bundle["Jimg"].shape
    Hs, Ws = int(H * supersample), int(W * supersample)
    xJ0 = np.asarray(bundle["xJ0"], float); xJ1 = np.asarray(bundle["xJ1"], float)
    lr0, lr1 = float(xJ1[0]), float(xJ1[-1])
    si0, si1 = float(xJ0[0]), float(xJ0[-1])
    # oriented-micron offset: xJ1 = X - X.mean() and c0 = X[0]  =>  X.mean() = c0 - xJ1[0]
    xmean = float(bundle["c0"]) - lr0
    ymean = float(bundle["r0"]) - si0

    # pixel centres of an edge-to-edge draw (matches the density draw path)
    lr = lr0 + (np.arange(Ws) + 0.5) * (lr1 - lr0) / Ws
    si = si0 + (np.arange(Hs) + 0.5) * (si1 - si0) / Hs
    LR, SI = np.meshgrid(lr, si)
    x_or = LR.ravel() + xmean
    y_or = SI.ravel() + ymean

    Morig = np.asarray(bundle["Morig"], float)          # (3, 2)
    orig = np.c_[x_or, y_or, np.ones_like(x_or)] @ Morig
    Mm = np.loadtxt(micron_to_mosaic_path(exp_path))
    if Mm.shape != (3, 3):
        return None
    px = Mm[0, 0] * orig[:, 0] + Mm[0, 1] * orig[:, 1] + Mm[0, 2]
    py = Mm[1, 0] * orig[:, 0] + Mm[1, 1] * orig[:, 1] + Mm[1, 2]

    samp = map_coordinates(small, [py / ds, px / ds], order=1,
                           mode="constant", cval=0.0)
    return np.clip(samp.reshape(Hs, Ws), 0, 1).astype(np.float32)


_dapi_small_cache = {}   # (exp_path, long) -> downscaled base for the orient step


def _small_dapi(exp_path, long=1600):
    """A copy of the DAPI base sized to `long` px on the long side, cached per
    size. The client fetches this once then rotates it with a CSS transform."""
    key = (exp_path, long)
    if key in _dapi_small_cache:
        return _dapi_small_cache[key]
    base = _load_dapi_base(exp_path)
    if base is not None and max(base.shape) > long:
        im = Image.fromarray((np.clip(base, 0, 1) * 255).astype(np.uint8))
        im.thumbnail((long, long), Image.LANCZOS)
        base = np.asarray(im, dtype=np.float32) / 255.0
    _dapi_small_cache[key] = base
    return base


def _density(exp_path, angle, flip, dx=RES):
    import pandas as pd
    m = pd.read_csv(os.path.join(exp_path, "cell_metadata.csv"),
                    usecols=["center_x", "center_y"])
    x, y = config.orient_apply(m["center_x"].to_numpy(), m["center_y"].to_numpy(),
                               angle, None if flip in (None, "none") else flip)
    nx = max(2, int((x.max() - x.min()) / dx))
    ny = max(2, int((y.max() - y.min()) / dx))
    M, _, _ = np.histogram2d(y, x, bins=[ny, nx])
    return M


def render_base_png(exp_path, max_px=1600):
    """Un-rotated DAPI base for the orientation step. The browser does the live
    rotate/flip with a CSS transform; this image is fetched once per step, so we
    can afford a crisp ~1600px render (clamped to 2000) to keep rotated edges
    smooth. High-quality LANCZOS resampling throughout."""
    max_px = int(np.clip(max_px, 400, 2000))
    base = _small_dapi(exp_path, long=max_px)
    if base is not None:
        return _to_png(base, max_px=max_px)
    # no DAPI: raw cell-density (sqrt-stretched), still un-oriented
    d = _density(exp_path, 0.0, "none") ** 0.5
    return _to_png(d / (d.max() + 1e-6), max_px=max_px)



ATLAS_MIN_PX = 900   # long side of the delivered atlas PNG (see render_atlas_png)


# The render is a pure function of (idx, alpha, min_px) and the AP slider sweeps
# back and forth over the same indices, so memoize the encoded PNG. 64 entries of a
# 900px slice is ~16 MB, and it turns a repeat tick from ~95 ms into a dict lookup.
@functools.lru_cache(maxsize=64)
def render_atlas_png(idx, alpha=0.45, min_px=ATLAS_MIN_PX, plane=None):
    """CCF reference slice with HPF (red) and ventricles (cyan) tinted, as PNG.

    A 50 um coronal slice is only ~228x160 px natively, and the client's CSS caps
    with max-width/max-height, which shrink but never enlarge -- so the atlas used
    to render at 228x160 inside a ~582x849 stage, far smaller than the tissue beside
    it. We therefore UPSAMPLE here to `min_px` on the long side.

    The upsample happens BEFORE compositing and treats the two layers differently:
    the greyscale reference is resized with LANCZOS (smooth, no blocky edges) while
    the region masks are resized with NEAREST, so the landmark boundaries the user
    is asked to match stay crisp instead of being blurred into a gradient.
    """
    from allen_alignment import planes as _planes
    at = api.get_atlas()
    hpf, vs = _region_ids("HPF"), _region_ids("VS")
    # The slice axis, and therefore the slice COUNT, depends on the plane: 264
    # coronal slices but 228 sagittal ones, so clamping against shape[0] would
    # silently accept an out-of-range sagittal index.
    plane = _planes.normalize(plane)
    idx = int(np.clip(idx, 0, _planes.n_slices(at.reference.shape, plane) - 1))
    ref = _planes.atlas_slice(at.reference, idx, plane).astype(np.float32)
    ref = ref / (ref.max() + 1e-6)
    ann = _planes.atlas_slice(at.annotation, idx, plane)
    hmask = np.isin(ann, list(hpf))
    vmask = np.isin(ann, list(vs))

    h, w = ref.shape
    if min_px and max(h, w) < min_px:
        s = min_px / float(max(h, w))
        size = (int(round(w * s)), int(round(h * s)))       # PIL is (W, H)
        ref = np.asarray(
            Image.fromarray((np.clip(ref, 0, 1) * 255).astype(np.uint8))
            .resize(size, Image.LANCZOS), dtype=np.float32) / 255.0
        hmask = np.asarray(Image.fromarray(hmask.astype(np.uint8) * 255)
                           .resize(size, Image.NEAREST)) > 127
        vmask = np.asarray(Image.fromarray(vmask.astype(np.uint8) * 255)
                           .resize(size, Image.NEAREST)) > 127

    rgb = np.repeat(ref[:, :, None], 3, axis=2)
    rgb[hmask] = (1 - alpha) * rgb[hmask] + alpha * np.array([1.0, 0.15, 0.15])
    rgb[vmask] = (1 - alpha) * rgb[vmask] + alpha * np.array([0.15, 0.85, 1.0])
    # max_px must not undo the upsample we just did
    return _to_png(np.clip(rgb, 0, 1), max_px=max(_TO_PNG_MAX_PX, min_px or 0))
