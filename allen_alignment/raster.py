"""The registration's numerical core in numpy: no torch, no STalign.

Three operations the default (affine-only) registration needs, and nothing else:

* ``density_raster``  -- a cell-density image on a regular grid, each cell spread
  as a small Gaussian;
* ``minmax``          -- linear rescale to [0, 1];
* ``sample_nearest``  -- read a 3D label volume at arbitrary points, nearest voxel.

They reproduce the conventions MACAT has always used -- the grid extent and
spacing, the kernel width, the voxel-centre convention of the atlas lookup -- so
registrations made before and after this module give the same labels
(tests/test_raster.py). They are written for this
project from those conventions, not taken from STalign (GPL-3.0), which MACAT no
longer uses at all (removed 2026-10-27).
"""
import numpy as np

__all__ = ["density_raster", "minmax", "sample_nearest"]


def density_raster(x, y, dx, *, blur=1.0, expand=1.1):
    """Cell density on a regular grid.

    The grid covers the cells' bounding box widened by ``expand`` about its
    centre, with pixel centres every ``dx`` from the low corner. Each cell adds a
    Gaussian of standard deviation ``2 * blur * dx`` over the pixels within
    ``ceil(4 * blur)`` of its nearest pixel (that window clipped to the grid),
    scaled so the cell contributes exactly 1 in total. Cells are added in input
    order, so the result does not depend on how the work is batched.

    Returns ``(xs, ys, M)``: pixel-centre coordinates along x (columns) and y
    (rows), and the image, shape ``(len(ys), len(xs))``.
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    y = np.asarray(y, dtype=np.float64).ravel()
    lo_x, hi_x = x.min(), x.max()
    lo_y, hi_y = y.min(), y.max()
    mid_x, half_x = (lo_x + hi_x) / 2.0, (hi_x - lo_x) / 2.0 * expand
    mid_y, half_y = (lo_y + hi_y) / 2.0, (hi_y - lo_y) / 2.0 * expand
    xs = np.arange(mid_x - half_x, mid_x + half_x, dx)
    ys = np.arange(mid_y - half_y, mid_y + half_y, dx)
    h, w = len(ys), len(xs)
    M = np.zeros(h * w, dtype=np.float64)
    if x.size == 0:
        return xs, ys, M.reshape(h, w)

    reach = int(np.ceil(4.0 * blur))
    two_var = 2.0 * (dx * blur * 2.0) ** 2
    near_c = np.round((x - xs[0]) / dx).astype(int)
    near_r = np.round((y - ys[0]) / dx).astype(int)
    r_lo = np.clip(near_r - reach, 0, h - 1); r_hi = np.clip(near_r + reach, 0, h - 1)
    c_lo = np.clip(near_c - reach, 0, w - 1); c_hi = np.clip(near_c + reach, 0, w - 1)

    # Each cell's window as a fixed (2*reach+1)^2 stencil starting at its clipped
    # low corner; offsets past the clipped high corner are masked out. Done in
    # chunks of cells so memory stays bounded on million-cell sections; chunks
    # run in input order, so the accumulation order is unchanged.
    span = np.arange(2 * reach + 1)
    for s in range(0, x.size, _CHUNK):
        e = s + _CHUNK
        rows = r_lo[s:e, None, None] + span[None, :, None]      # (n, s, 1)
        cols = c_lo[s:e, None, None] + span[None, None, :]      # (n, 1, s)
        keep = (rows <= r_hi[s:e, None, None]) & (cols <= c_hi[s:e, None, None])
        rows = np.minimum(rows, h - 1); cols = np.minimum(cols, w - 1)
        g = np.exp(-((xs[cols] - x[s:e, None, None]) ** 2
                     + (ys[rows] - y[s:e, None, None]) ** 2) / two_var)
        g = np.where(keep, g, 0.0)
        g /= g.sum(axis=(1, 2), keepdims=True)
        # cell-major order: every pixel receives its cells' shares in input order
        np.add.at(M, (rows * w + cols)[keep], g[keep])
    return xs, ys, M.reshape(h, w)


_CHUNK = 50_000


def minmax(a):
    """Rescale linearly so the minimum maps to 0 and the maximum to 1."""
    a = np.asarray(a, dtype=np.float64)
    lo = a.min()
    return (a - lo) / (a.max() - lo)


def sample_nearest(axes, volume, points):
    """Values of ``volume`` at ``points``, taking the nearest voxel.

    ``axes`` holds the voxel-centre coordinates along each of the volume's three
    axes (evenly spaced, ascending); ``points`` has shape ``(..., 3)`` in the same
    coordinates and axis order. A point whose nearest voxel lies outside the
    volume gets 0. Halfway cases round to the even index, so a point exactly
    between two voxels resolves the same way every time.
    """
    pts = np.asarray(points, dtype=np.float64)
    out = np.zeros(pts.shape[:-1], dtype=volume.dtype)
    inside = np.ones(pts.shape[:-1], dtype=bool)
    idx = []
    for k in range(3):
        a = np.asarray(axes[k], dtype=np.float64)
        n = volume.shape[k]
        # position as a fraction of the axis span, then as a fractional index
        u = (pts[..., k] - a[0]) / (a[-1] - a[0]) * 2.0 - 1.0
        i = np.rint((u + 1.0) / 2.0 * (n - 1))
        inside &= (i >= 0) & (i <= n - 1)
        idx.append(i)
    sel = tuple(np.where(inside, i, 0).astype(np.intp) for i in idx)
    out[inside] = volume[sel][inside]
    return out
