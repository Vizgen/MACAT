"""Plane-grid geometry: the pixel <-> micron conversions every other module
shares. Deliberately dependency-free so nothing has to import a heavier module
just to convert a coordinate."""
import numpy as np


# ============================================================ plane-grid geometry
# Coordinates are plane microns [lr, si], matching the interactive canvas.

def _plane_spacing(bundle):
    xJ0 = bundle["xJ0"]; xJ1 = bundle["xJ1"]
    dSI = float(xJ0[1] - xJ0[0]); dLR = float(xJ1[1] - xJ1[0])
    return xJ0, xJ1, dSI, dLR



def _px_to_um(bundle, pts_px):
    """[row, col] grid pixels -> [lr, si] microns."""
    xJ0, xJ1, dSI, dLR = _plane_spacing(bundle)
    p = np.asarray(pts_px, float).reshape(-1, 2)
    return np.c_[xJ1[0] + p[:, 1] * dLR, xJ0[0] + p[:, 0] * dSI]


def closed_contours(mask, level=0.5):
    """``skimage.measure.find_contours`` that never leaves a region open.

    A region touching the edge of the plane raster comes back from
    find_contours as an OPEN contour, ending where it meets the edge. Closed by
    joining its last point to its first, as every outline and polygon here does,
    that draws a straight chord across the region. Whole sections rarely touch
    the edge. A hemisection does on its midline side, and on the positive-control
    hemisections the chord ran diagonally across the whole cortex (2026-10-21).

    Padding the mask with a one-pixel border of zeros makes every contour close
    along the raster edge instead, which is the real boundary of what was
    sampled; the shift is undone, so coordinates stay in the unpadded frame. A
    region that does not touch the edge gets the same contour as before.
    """
    from skimage import measure
    padded = np.pad(np.asarray(mask, dtype=float), 1, mode="constant")
    return [c - 1.0 for c in measure.find_contours(padded, level)]
