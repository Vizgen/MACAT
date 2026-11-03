"""Overlay geometry: turning a label raster into the contours, region labels and
fills the curation canvas and the result figures draw.

Read-only by construction -- the cleanup here (component pruning, hole
reabsorption, simplification) affects the OVERLAY only and never the leaf-level
label raster or any export."""
import numpy as np
from .plane import (_plane_spacing, _px_to_um, closed_contours)
from .atlas import (COARSE_REGION_DEFAULT_COLOR, CURATION_TIER, DEPTH2_LEVEL, LANDMARK_TIERS, _coarsen_ids, _group_by_depth2, _group_by_landmarks, _load_structure_tree, get_atlas, region_style)


def bundle_boundaries(bundle, depth=None, atlas=None, max_pts=400):
    """DEBUG view only: the full Allen region boundaries as polylines in plane
    microns [lr, si], at a chosen ontology density (depth). These are NOT
    editable — they are a reference/debug overlay. Manual curation uses the
    sparse curated set from `landmark_boundaries` instead."""
    from skimage import measure
    atlas = atlas or get_atlas()
    idmap = _coarsen_ids(bundle, depth, atlas=atlas)
    id2acro = bundle["id2acro"]; id2name = bundle["id2name"]
    feats = []
    for u in np.unique(idmap):
        if u <= 0:
            continue
        for ct in closed_contours(idmap == u):
            if len(ct) < 4:
                continue
            if len(ct) > max_pts:
                ct = ct[:: int(np.ceil(len(ct) / max_pts))]
            um = _px_to_um(bundle, ct)
            feats.append({"id": int(u),
                          "acronym": id2acro.get(int(u), str(int(u))),
                          "name": id2name.get(int(u), ""),
                          "kind": "region", "editable": False,
                          "points": [[round(float(a), 1), round(float(b), 1)]
                                     for a, b in um]})
    return feats



# Major coarse regions get an interior acronym label (Allen-atlas style). We only
# label components large enough to read; slivers and secondary structure stay
# unlabelled so the overlay never gets as dense as the full atlas.
CURATION_MIN_LABEL_AREA_UM2 = 200000.0    # component smaller than this -> no label



def region_labels(bundle, tier=CURATION_TIER, atlas=None,
                  min_area_um2=CURATION_MIN_LABEL_AREA_UM2):
    """Interior label anchors for the MAJOR coarse regions in this plane. For each
    coarse division, every connected component whose area exceeds ``min_area_um2``
    gets ONE label at its distance-transform peak (pole of inaccessibility), so
    the acronym sits at the visual centre and never on a boundary line. Returns
    dicts in plane microns [lr, si] with the shared short acronym + colour."""
    from scipy import ndimage
    atlas = atlas or get_atlas()
    acronyms = LANDMARK_TIERS.get(tier, LANDMARK_TIERS["landmarks"])
    idmap, label_acro = _group_by_landmarks(bundle, acronyms, atlas)
    _, _, dSI, dLR = _plane_spacing(bundle)
    px_area = abs(dSI * dLR)
    out = []
    for gid, acro in label_acro.items():
        lbl, n = ndimage.label(idmap == gid)
        for comp in range(1, n + 1):
            cm = lbl == comp
            area = float(cm.sum()) * px_area
            if area < min_area_um2:
                continue
            dt = ndimage.distance_transform_edt(cm)
            r, c = np.unravel_index(int(np.argmax(dt)), dt.shape)
            lr, si = _px_to_um(bundle, [[r, c]])[0]
            st = region_style(acro)
            try:
                name = atlas.structures[int(gid)]["name"]
            except Exception:
                name = acro
            out.append({"gid": int(gid), "acronym": acro, "short": st["short"],
                        "color": st["color"], "name": name,
                        "lr": round(float(lr), 1), "si": round(float(si), 1),
                        "area_um2": round(area, 1)})
    out.sort(key=lambda d: -d["area_um2"])
    return out



# --- overlay cleanup thresholds (physical, relative to tissue -- never hard px) --
DEPTH2_COMP_AREA_FRAC = 0.0006   # drop region components smaller than this frac of
                                 # the tissue area (removes speckle / stray leaves)

DEPTH2_MIN_COMP_PX = 12          # absolute floor so tiny rasters still clean up

DEPTH2_MIN_EDGE_FRAC = 0.035     # drop boundary polylines shorter than this frac of
                                 # the tissue characteristic length sqrt(area)

DEPTH2_MIN_EDGE_FLOOR_UM = 120.0 # absolute short-fragment floor (microns)



def _rdp(pts, eps):
    """Iterative Ramer-Douglas-Peucker simplification of an [N,2] polyline. Turns
    marching-squares staircases into a few straight segments (eps in the same
    units as pts, here microns)."""
    pts = np.asarray(pts, float)
    n = len(pts)
    if n < 3:
        return pts
    keep = np.zeros(n, bool); keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        a = pts[i0]; b = pts[i1]; ab = b - a
        L = float(np.hypot(*ab))
        seg = pts[i0 + 1:i1]
        if len(seg) == 0:
            continue
        if L < 1e-9:
            d = np.hypot(*(seg - a).T)
        else:
            d = np.abs(np.cross(ab, seg - a)) / L
        k = int(np.argmax(d))
        if d[k] > eps:
            idx = i0 + 1 + k
            keep[idx] = True
            stack.append((i0, idx)); stack.append((idx, i1))
    return pts[keep]



def _depth2_clean(bundle, level=DEPTH2_LEVEL):
    """Cleaned hierarchy-level categorical raster shared by BOTH the boundary overlay and
    the interior labels (so Registration and Curation are pixel-identical). Removes
    sub-threshold region components, then reabsorbs the freed voids AND any small
    enclosed holes into the nearest surviving region via a distance transform. The
    LEAF-level annotation raster (AphiL) and all exports are untouched -- this only
    feeds the read-only overlay."""
    from scipy import ndimage
    idmap, meta = _group_by_depth2(bundle, level=level)
    tissue = np.asarray(bundle["AphiL"]) > 0
    _, _, dSI, dLR = _plane_spacing(bundle)
    px_area = abs(dSI * dLR)
    min_comp_px = max(DEPTH2_MIN_COMP_PX,
                      int(DEPTH2_COMP_AREA_FRAC * float(tissue.sum())))
    clean = np.zeros_like(idmap)
    for gid in np.unique(idmap):
        if gid <= 0:
            continue
        lbl, n = ndimage.label(idmap == gid)
        if n == 0:
            continue
        counts = np.bincount(lbl.ravel())
        keep = np.zeros(idmap.shape, bool)
        for comp in range(1, n + 1):
            if counts[comp] >= min_comp_px:
                keep |= (lbl == comp)
        clean[keep] = gid
    # reabsorb ONLY the holes freed by sub-threshold speckle removal (had a valid
    # depth-2 group but lost it) into the nearest surviving region. Genuinely
    # unmapped voxels (no depth-2 ancestor -> idmap==0) are LEFT as background.
    void = (idmap > 0) & (clean == 0)
    if void.any() and (clean > 0).any():
        idx = ndimage.distance_transform_edt(clean == 0, return_indices=True)[1]
        clean = np.where(void, clean[tuple(idx)], clean)
    return clean, meta, tissue, px_area



def _closed_mask_rings(mask, tolerance_px=0.9, max_pts=400):
    """Return closed exterior/hole rings for one connected binary component.

    Padding guarantees that components touching the raster edge still produce a
    closed contour.  The largest contour is the exterior; every other contour is
    an interior hole.  Points are pixel ``[row, col]`` coordinates.
    """
    from skimage import measure
    padded = np.pad(np.asarray(mask, bool), 1, mode="constant")
    contours = measure.find_contours(padded.astype(float), 0.5)
    rings = []
    for contour in contours:
        ring = np.asarray(contour, float) - 1.0
        if len(ring) < 3:
            continue
        if not np.array_equal(ring[0], ring[-1]):
            ring = np.vstack([ring, ring[0]])
        ring = measure.approximate_polygon(ring, tolerance=float(tolerance_px))
        if len(ring) < 4:
            continue
        if not np.array_equal(ring[0], ring[-1]):
            ring = np.vstack([ring, ring[0]])
        if len(ring) > max_pts:
            # Sample the open portion, then restore the exact first point.  Never
            # simplify a ring into an open polyline.
            body = ring[:-1]
            step = int(np.ceil(len(body) / max(max_pts - 1, 3)))
            body = body[::step]
            ring = np.vstack([body, body[0]])
        # Shoelace area in (col,row); absolute area is enough for nesting here
        # because the input is exactly one connected component.
        xy = ring[:, [1, 0]]
        area = 0.5 * abs(float(np.sum(
            xy[:-1, 0] * xy[1:, 1] - xy[1:, 0] * xy[:-1, 1]
        )))
        if area > 0:
            rings.append((area, ring))
    if not rings:
        return []
    rings.sort(key=lambda item: -item[0])
    return [{"role": "exterior" if i == 0 else "hole", "points": ring}
            for i, (_, ring) in enumerate(rings)]



def depth2_boundaries(bundle, atlas=None, max_pts=400, outer=True, midline=True,
                      level=DEPTH2_LEVEL):
    """Closed hierarchy-level region polygons in plane microns ``[lr, si]``.

    Every connected component of every surviving category is represented by one
    exterior ring plus zero or more interior-hole rings.  Shared borders may be
    drawn twice so each component remains topologically closed.  The tissue outer
    contour and anatomical midline remain separate read-only features.  This is the
    single generator shared by Registration and Curation.
    """
    from skimage import measure
    from scipy import ndimage
    idmap, meta, tissue, px_area = _depth2_clean(bundle, level=level)
    _, _, dSI, dLR = _plane_spacing(bundle)
    eps_um = 0.9 * max(abs(dSI), abs(dLR))            # ~1px staircase tolerance
    tolerance_px = eps_um / max(abs(dSI), abs(dLR), 1e-9)
    char_um = float(np.sqrt(max(float(tissue.sum()) * px_area, 1.0)))
    min_len_um = max(DEPTH2_MIN_EDGE_FLOOR_UM, DEPTH2_MIN_EDGE_FRAC * char_um)
    feats = []
    dropped = [0]

    def _len_um(um):
        return float(np.hypot(*np.diff(um, axis=0).T).sum()) if len(um) > 1 else 0.0

    def emit(um, sid, acro, name, kind, color, min_len=0.0):
        um = _rdp(np.asarray(um, float), eps_um)
        if len(um) < 2:
            return
        if min_len and _len_um(um) < min_len:
            dropped[0] += 1
            return
        if len(um) > max_pts:
            um = um[:: int(np.ceil(len(um) / max_pts))]
        feats.append({"id": (int(sid) if sid else None),
                      "acronym": acro, "name": name, "kind": kind,
                      "color": color, "editable": False,
                      "points": [[round(float(a), 1), round(float(b), 1)]
                                 for a, b in um]})

    if outer:
        tclean = ndimage.binary_fill_holes(tissue)
        for ct in closed_contours(tclean):
            emit(_px_to_um(bundle, ct), None, "tissue", "Outer contour",
                 "outer", "#dfeaf5", min_len=min_len_um)

    for gid in np.unique(idmap):
        if gid <= 0:
            continue
        info = meta.get(int(gid), {})
        labeled, ncomp = ndimage.label(idmap == gid)
        for component in range(1, ncomp + 1):
            cmask = labeled == component
            rings_px = _closed_mask_rings(
                cmask, tolerance_px=tolerance_px, max_pts=max_pts)
            if not rings_px:
                continue
            rings = []
            for ring in rings_px:
                um = _px_to_um(bundle, ring["points"])
                # Conversion and rounding must not introduce a sub-pixel seam.
                pts = [[round(float(a), 1), round(float(b), 1)] for a, b in um]
                pts[-1] = list(pts[0])
                rings.append({"role": ring["role"], "points": pts})
            acro = info.get("acronym", str(int(gid)))
            feats.append({
                "id": int(gid), "acronym": acro,
                "name": info.get("name", acro), "kind": "region",
                "geometry": "polygon", "component": int(component),
                "color": info.get("color", COARSE_REGION_DEFAULT_COLOR),
                "editable": False, "rings": rings,
                # Measured here rather than by any client: `cmask` and `px_area`
                # are already in hand, so this costs one bincount on a raster we
                # just built. Every drawn component carries it, including the ones
                # below CURATION_MIN_LABEL_AREA_UM2 that never get a label -- which
                # is what lets the curation legend order its whole pill list.
                # This is the area of the component on THIS section's cleaned
                # plane raster, so it is not a property of the AP index alone.
                "area_um2": round(float(cmask.sum()) * px_area, 1),
            })

    if midline and "tf" in bundle:
        ml = np.asarray(bundle["tf"])[..., 2]
        for ct in measure.find_contours(ml, 0.0):
            emit(_px_to_um(bundle, ct), None, "midline", "Midline (ML=0)",
                 "midline", "#7ee0d6", min_len=min_len_um)
    depth2_boundaries.last_dropped = dropped[0]
    return feats



# The sparse guide set the UI calls "Landmarks" (mode id "alignment"). These are
# NOT a hierarchy level: they sit at four different Allen depths -- HPF 5, TH 4,
# VL 2, V3 2 -- and are chosen for being high-contrast and unambiguous on a
# coronal section, which is what you actually align against. Allen depth 1 would
# be grey / fiber tracts / VS / grv / retina, i.e. three or four huge blobs.
ALIGNMENT_GUIDE_SPECS = (
    # acronym, maximum connected components, optional in the UI
    ("HPF", 2, False),
    ("VL", 2, False),
    ("V3", 1, False),
    ("TH", 2, True),
)



def alignment_region_boundaries(bundle, max_pts=400, outer=True):
    """Sparse, named alignment guides derived from the CURRENT AP leaf raster.

    Each guide is the exterior boundary of a real Allen region or a union of its
    real descendants.  At most one representative ring is emitted per connected
    component, so nested leaf boundaries never clutter the alignment view.  The
    full depth-2 polygons remain available separately and final leaf labels are
    untouched.
    """
    from skimage import measure
    from scipy import ndimage

    S = _load_structure_tree()
    A = np.asarray(bundle["AphiL"])
    tissue = A > 0
    _, _, dSI, dLR = _plane_spacing(bundle)
    eps_um = 0.9 * max(abs(dSI), abs(dLR))
    tolerance_px = eps_um / max(abs(dSI), abs(dLR), 1e-9)
    min_comp_px = max(DEPTH2_MIN_COMP_PX,
                      int(DEPTH2_COMP_AREA_FRAC * float(tissue.sum())))
    by_acronym = {v.get("acronym"): int(k) for k, v in S.items()}
    feats = []

    if outer:
        tclean = ndimage.binary_fill_holes(tissue)
        contours = closed_contours(tclean)
        if contours:
            ct = max(contours, key=len)
            pts = _px_to_um(bundle, ct)
            pts = _rdp(np.asarray(pts, float), eps_um)
            if len(pts) > max_pts:
                pts = pts[::int(np.ceil(len(pts) / max_pts))]
            closed = [[round(float(a), 1), round(float(b), 1)] for a, b in pts]
            if closed:
                closed.append(list(closed[0]))
                feats.append({
                    "id": None, "acronym": "tissue", "name": "Outer tissue contour",
                    "kind": "outer", "geometry": "polygon", "component": 1,
                    "color": "#dfeaf5", "editable": False,
                    "guide_family": "tissue", "optional": False,
                    "members": [], "source_ids": [],
                    "rings": [{"role": "exterior", "points": closed}],
                })

    unique_ids = [int(x) for x in np.unique(A) if int(x) > 0]
    for acronym, max_components, optional in ALIGNMENT_GUIDE_SPECS:
        target_id = by_acronym.get(acronym)
        if not target_id:
            continue
        member_ids = [
            sid for sid in unique_ids
            if sid == target_id or target_id in S.get(sid, {}).get("path", [])
        ]
        if not member_ids:
            continue
        mask = np.isin(A, member_ids)
        labeled, ncomp = ndimage.label(mask)
        components = []
        for component in range(1, ncomp + 1):
            cmask = labeled == component
            area = int(cmask.sum())
            if area >= min_comp_px:
                components.append((area, component, cmask))
        components.sort(key=lambda x: x[0], reverse=True)

        member_meta = [S.get(sid, {}) for sid in member_ids]
        members = sorted({
            m.get("acronym", str(sid))
            for sid, m in zip(member_ids, member_meta)
        })
        target = S.get(target_id, {})
        for rank, (_, component, cmask) in enumerate(
                components[:max_components], start=1):
            rings_px = _closed_mask_rings(
                cmask, tolerance_px=tolerance_px, max_pts=max_pts)
            exterior = next((r for r in rings_px if r["role"] == "exterior"), None)
            if exterior is None:
                continue
            um = _px_to_um(bundle, exterior["points"])
            points = [[round(float(a), 1), round(float(b), 1)] for a, b in um]
            if not points:
                continue
            points[-1] = list(points[0])
            feats.append({
                "id": int(target_id), "acronym": acronym,
                "name": target.get("name", acronym),
                "kind": "alignment_region", "geometry": "polygon",
                "component": int(rank),
                "color": target.get("color", "#bde9ff"),
                "editable": False, "guide_family": acronym,
                "optional": bool(optional), "members": members,
                "source_ids": member_ids,
                "rings": [{"role": "exterior", "points": points}],
            })
    return feats



def depth2_region_labels(bundle, min_area_um2=CURATION_MIN_LABEL_AREA_UM2,
                         level=DEPTH2_LEVEL):
    """Interior acronym anchors for the selected hierarchy level on the
    SAME cleaned raster as the boundaries. One label per surviving connected
    component larger than ``min_area_um2``, placed at the component's
    distance-transform peak so it sits at the visual centre."""
    from scipy import ndimage
    idmap, meta, _, px_area = _depth2_clean(bundle, level=level)
    out = []
    for gid in np.unique(idmap):
        if gid <= 0:
            continue
        m = meta.get(int(gid), {})
        lbl, n = ndimage.label(idmap == gid)
        for comp in range(1, n + 1):
            cm = lbl == comp
            area = float(cm.sum()) * px_area
            if area < min_area_um2:
                continue
            dt = ndimage.distance_transform_edt(cm)
            r, c = np.unravel_index(int(np.argmax(dt)), dt.shape)
            lr, si = _px_to_um(bundle, [[r, c]])[0]
            acro = m.get("acronym", str(int(gid)))
            out.append({"gid": int(gid), "acronym": acro, "short": acro,
                        "component": int(comp),
                        "color": m.get("color", COARSE_REGION_DEFAULT_COLOR),
                        "name": m.get("name", acro),
                        "lr": round(float(lr), 1), "si": round(float(si), 1),
                        "area_um2": round(area, 1)})
    out.sort(key=lambda d: -d["area_um2"])
    return out



# Translucency used by rendered registration and curated result figures. The
# interactive canvas draws polygon fills client-side with a user-adjustable alpha.
DEPTH2_FILL_ALPHA_RESULTS = 0.32



def _hex_to_rgb(h):
    h = (h or "").lstrip("#")
    if len(h) != 6:
        h = "8aa0b8"
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)



def depth2_fill_rgba(bundle, alpha=DEPTH2_FILL_ALPHA_RESULTS,
                     level=DEPTH2_LEVEL):
    """Semi-transparent, official-colour categorical FILL of the cleaned depth-2
    raster as an (H, W, 4) uint8 RGBA image. This is the SAME `_depth2_clean` raster
    that produces the boundaries and interior labels, so the fill, borders and
    legend are pixel-consistent in both the registration preview and the curation
    canvas. Unmapped / background pixels are fully transparent (alpha 0); the
    translucency is baked in so matplotlib (imshow) and the browser canvas match.

    `alpha` defaults to the interactive-canvas value; the rendered result figures
    pass DEPTH2_FILL_ALPHA_RESULTS. Colours are unaffected either way."""
    clean, meta, _, _ = _depth2_clean(bundle, level=level)
    H, W = clean.shape
    rgba = np.zeros((H, W, 4), np.uint8)
    a = int(round(255 * float(alpha)))
    for gid in np.unique(clean):
        if gid <= 0:
            continue
        m = meta.get(int(gid), {})
        r, g, b = _hex_to_rgb(m.get("color", COARSE_REGION_DEFAULT_COLOR))
        mask = clean == gid
        rgba[mask, 0] = r
        rgba[mask, 1] = g
        rgba[mask, 2] = b
        rgba[mask, 3] = a
    return rgba



def _trace_arrangement(Lc):
    """Trace the planar arrangement (region-boundary graph) of an integer label
    image ``Lc`` (``-1`` = exterior). Boundaries live on the pixel *cracks*; a
    NODE is a pixel-corner where != 2 boundary cracks meet or where the pair of
    labels being separated changes (a junction of >=3 faces). An EDGE is the
    maximal chain of cracks between two nodes that separates a *single* label
    pair -- so every shared border between two regions is traced exactly ONCE.

    Returns a list of dicts: ``path`` = padded-corner ``(i, j)`` coords,
    ``pair`` = ``frozenset`` of the two labels, ``node_a``/``node_b`` = the
    junction corner at each end (``None`` for a closed loop). Edges that share a
    node have that corner as an *identical* endpoint, which is what lets the
    curation UI keep connected boundaries glued when one is dragged."""
    from collections import defaultdict
    H, W = Lc.shape
    Lp = np.full((H + 2, W + 2), -1, dtype=np.int64)   # pad with exterior
    Lp[1:-1, 1:-1] = Lc
    H2, W2 = Lp.shape
    inc = defaultdict(list)                             # corner -> [(nbr, pair)]

    # horizontal cracks: between pixel rows i-1 / i, corners (i,j)-(i,j+1)
    for i in range(1, H2):
        ra, rb = Lp[i - 1], Lp[i]
        for j in np.where(ra != rb)[0]:
            pr = frozenset((int(ra[j]), int(rb[j])))
            c0, c1 = (i, int(j)), (i, int(j) + 1)
            inc[c0].append((c1, pr)); inc[c1].append((c0, pr))
    # vertical cracks: between pixel cols j-1 / j, corners (i,j)-(i+1,j)
    for j in range(1, W2):
        ca, cb = Lp[:, j - 1], Lp[:, j]
        for i in np.where(ca != cb)[0]:
            pr = frozenset((int(ca[i]), int(cb[i])))
            c0, c1 = (int(i), j), (int(i) + 1, j)
            inc[c0].append((c1, pr)); inc[c1].append((c0, pr))

    def is_node(c):
        lst = inc[c]
        return len(lst) != 2 or lst[0][1] != lst[1][1]

    used = set()
    ck = lambda a, b: (a, b) if a <= b else (b, a)

    def walk(start, nbr, pr):
        path = [start, nbr]; used.add(ck(start, nbr)); prev, cur = start, nbr
        while not (cur == start or is_node(cur)):
            nxt = None
            for nb, p2 in inc[cur]:
                if nb != prev and p2 == pr and ck(cur, nb) not in used:
                    nxt = nb; break
            if nxt is None:
                break
            used.add(ck(cur, nxt)); path.append(nxt); prev, cur = cur, nxt
        return path, cur

    edges = []
    for nd in [c for c in inc if is_node(c)]:           # node-to-node edges
        for nb, pr in inc[nd]:
            if ck(nd, nb) not in used:
                path, end = walk(nd, nb, pr)
                edges.append(dict(path=path, pair=pr, node_a=nd, node_b=end))
    for c in list(inc.keys()):                          # remaining closed loops
        for nb, pr in inc[c]:
            if ck(c, nb) not in used:
                path, _ = walk(c, nb, pr)
                edges.append(dict(path=path, pair=pr, node_a=None, node_b=None))
    return edges



def _resample_arclen(um, step, closed):
    """Resample a polyline (plane microns) to roughly uniform arc-length spacing
    so downstream smoothing acts evenly along the curve."""
    um = np.asarray(um, float)
    if len(um) < 3:
        return um
    if closed and not np.allclose(um[0], um[-1]):
        um = np.vstack([um, um[:1]])
    seg = np.hypot(*np.diff(um, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    L = float(s[-1])
    if L < step * 2:
        return um
    n = max(4, int(round(L / step)))
    snew = np.linspace(0, L, n, endpoint=not closed)
    x = np.interp(snew, s, um[:, 0]); y = np.interp(snew, s, um[:, 1])
    return np.column_stack([x, y])



def _smooth_polyline(um, closed, step=35.0, win=9, poly=2):
    """Light Savitzky-Golay smoothing of a visible boundary polyline to remove
    raster pixelation / jaggies WITHOUT reshaping the anatomy. Endpoints of open
    arcs are pinned; closed loops wrap. Operates only on the overlay geometry --
    never on the label raster."""
    from scipy.signal import savgol_filter
    r = _resample_arclen(um, step, closed)
    n = len(r)
    if n < 5:
        return np.asarray(um, float)
    w = win if win <= n else (n if n % 2 else n - 1)
    if w % 2 == 0:
        w -= 1
    if w < 5:
        return r
    p = min(poly, w - 1)
    mode = "wrap" if closed else "interp"
    x = savgol_filter(r[:, 0], w, p, mode=mode)
    y = savgol_filter(r[:, 1], w, p, mode=mode)
    out = np.column_stack([x, y])
    if closed:
        return np.vstack([out, out[:1]])            # keep the loop closed
    out[0] = r[0]; out[-1] = r[-1]                   # preserve exact endpoints
    return out



# ---- overlay-only cleanup of the arrangement (never touches the label raster) --
# Raster jaggies produce a few meaningless boundary fragments: isolated short
# lines, single-connected spurs / hooks, and tiny closed loops. These carry no
# manual-alignment meaning, so we drop them from the OVERLAY only. Thresholds are
# physical microns (dataset-independent) with wide safety margins to real anatomy.
CURATION_MIN_OPEN_COMP = 1000.0    # isolated open component shorter than this -> drop

CURATION_MIN_LOOP_AREA = 10000.0   # closed loop enclosing less area (um^2) -> drop

CURATION_SPUR_MAXLEN = 250.0       # single-connected dangling stub shorter -> trim



def _poly_len(p):
    return float(np.hypot(*np.diff(np.asarray(p, float), axis=0).T).sum())



def _poly_area(p):
    p = np.asarray(p, float); x, y = p[:, 0], p[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)



def _join_edges(e1, e2, v):
    """Concatenate two arrangement edges that meet at node id ``v`` into one
    polyline (used to merge same-region-pair fragments split by an artefact
    junction). Orientation is normalised so both meet head-to-tail at ``v``."""
    p1, a1, b1 = e1["pts"], e1["na"], e1["nb"]
    p2, a2, b2 = e2["pts"], e2["na"], e2["nb"]
    if b1 != v:
        p1 = p1[::-1]; a1, b1 = b1, a1
    if a2 != v:
        p2 = p2[::-1]; a2, b2 = b2, a2
    if b1 != v or a2 != v:
        return None
    pts = np.vstack([p1, p2[1:]])
    na, nb = a1, b2
    if na is not None and na == nb:                 # closed the loop -> no free ends
        na = nb = None
    return {"pts": pts, "pair": e1["pair"], "na": na, "nb": nb}



def _cleanup_edges(edges, min_open_comp=CURATION_MIN_OPEN_COMP,
                   min_loop_area=CURATION_MIN_LOOP_AREA,
                   spur_maxlen=CURATION_SPUR_MAXLEN):
    """Drop meaningless arrangement fragments and merge over artefact junctions.
    Operates purely on overlay geometry -- region classification is untouched."""
    if not edges:
        return edges

    # 1) remove ISOLATED small components (a connected group of edges detached
    #    from the main boundary network). Open groups judged by total length,
    #    pure closed loops by enclosed area, so real islands (large loops,
    #    long lateral chains) survive.
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        parent[find(a)] = find(b)
    for i, e in enumerate(edges):
        if e["na"] is not None and e["nb"] is not None:
            union(("n", e["na"]), ("n", e["nb"])); e["_ck"] = ("n", e["na"])
        elif e["na"] is not None:
            e["_ck"] = ("n", e["na"])
        elif e["nb"] is not None:
            e["_ck"] = ("n", e["nb"])
        else:
            e["_ck"] = ("e", i)
    comps = {}
    for e in edges:
        root = find(e["_ck"]) if e["_ck"][0] == "n" else e["_ck"]
        comps.setdefault(root, []).append(e)
    comp_len = lambda fs: sum(_poly_len(e["pts"]) for e in fs)
    main = max(comps.values(), key=comp_len)
    kept = []
    for fs in comps.values():
        if fs is main:
            kept += fs; continue
        is_loop = len(fs) == 1 and fs[0]["na"] is None and fs[0]["nb"] is None
        if is_loop:
            if _poly_area(fs[0]["pts"]) >= min_loop_area:
                kept += fs
        elif comp_len(fs) >= min_open_comp:
            kept += fs
    edges = [{k: v for k, v in e.items() if k != "_ck"} for e in kept]

    # 2) trim short single-connected spurs / hooks (one end at a real junction,
    #    the other dangling), iteratively so a freshly exposed stub also goes.
    changed = True
    while changed:
        changed = False
        fan = {}
        for e in edges:
            for n in (e["na"], e["nb"]):
                if n is not None:
                    fan[n] = fan.get(n, 0) + 1
        out = []
        for e in edges:
            closed = e["na"] is None and e["nb"] is None
            conn = sum(1 for n in (e["na"], e["nb"]) if n is not None and fan.get(n, 0) >= 2)
            if (not closed) and conn <= 1 and _poly_len(e["pts"]) < spur_maxlen:
                changed = True; continue            # drop the stub
            out.append(e)
        edges = out

    # 3) merge fragments of the SAME region pair that meet at a junction where
    #    ONLY those two edges connect (an artefact split) -> one longer curve.
    changed = True
    while changed:
        changed = False
        inc = {}
        for e in edges:
            for n in (e["na"], e["nb"]):
                if n is not None:
                    inc.setdefault(n, []).append(e)
        for n, lst in inc.items():
            if len(lst) == 2 and lst[0] is not lst[1] and lst[0]["pair"] == lst[1]["pair"]:
                merged = _join_edges(lst[0], lst[1], n)
                if merged is None:
                    continue
                edges = [e for e in edges if e is not lst[0] and e is not lst[1]]
                edges.append(merged); changed = True
                break
    return edges



# ---- planar-graph stitching: endpoint-to-SEGMENT (T-junction) connections -----
# The arrangement already shares a node wherever two REGION borders meet. But a
# region border that terminates on the tissue OUTER contour or the MIDLINE lands
# in the *interior* of those curves, which are traced separately and carry no
# nodes. That dangling endpoint is a T-junction: dragging either side leaves a
# gap because nothing ties them together. This pass builds the FULL planar graph
# ONCE (at generation): every dangling endpoint that sits on another curve's
# interior gets that curve SPLIT at the contact point, and the endpoint plus both
# split halves are given ONE shared node with an identical coordinate. The drag
# UI (unchanged) then keeps the junction glued via the shared node.
CURATION_TJUNC_TOL = 60.0        # um: attach a dangling endpoint onto a curve closer than this

CURATION_TJUNC_ENDMARGIN = 60.0  # um: contact must be this far from the target curve's own ends



def _is_closed_poly(p):
    return len(p) > 3 and bool(np.allclose(p[0], p[-1]))



def _project_point_polyline(P, Q):
    """Closest point on polyline ``Q`` to point ``P``. Returns
    (dist, seg_index, proj_point, arclen_pos, total_len)."""
    best = (1e18, 0, Q[0].copy(), 0.0)
    acc = 0.0
    for k in range(len(Q) - 1):
        a = Q[k]; ab = Q[k + 1] - a; L2 = float(ab @ ab)
        t = 0.0 if L2 == 0.0 else float(np.clip((P - a) @ ab / L2, 0.0, 1.0))
        proj = a + t * ab
        d = float(np.hypot(*(P - proj)))
        seg = float(np.sqrt(L2))
        if d < best[0]:
            best = (d, k, proj, acc + t * seg)
        acc += seg
    return best[0], best[1], best[2], best[3], acc



def _stitch_tjunctions(feats, tol=CURATION_TJUNC_TOL,
                       endmargin=CURATION_TJUNC_ENDMARGIN):
    """Resolve endpoint-to-segment T-junctions into shared nodes (see note above).
    Runs once; returns a new feature list where affected target curves are split
    and all incident edges share an identical node coordinate. Metadata, region
    classification and the drag algorithm are untouched."""
    edges = [{"pts": np.asarray(f["points"], float),
              "na": f.get("node_a"), "nb": f.get("node_b"), "f": f}
             for f in feats]
    node_ids = [e[k] for e in edges for k in ("na", "nb") if e[k] is not None]
    next_id = (max(node_ids) + 1) if node_ids else 0

    def fan_counts():
        fan = {}
        for e in edges:
            for nid in (e["na"], e["nb"]):
                if nid is not None:
                    fan[nid] = fan.get(nid, 0) + 1
        return fan

    def split(g, seg_i, proj, nid):
        p = g["pts"]
        if _is_closed_poly(p) and g["na"] is None:        # genuine closed loop (no node yet)
            pc = p[:-1]                                   # unique ring vertices
            m = len(pc); j = (seg_i + 1) % m
            ring = np.vstack([proj, pc[j:], pc[:j], proj])  # open the loop AT proj
            new = [{"pts": ring, "na": nid, "nb": nid, "f": g["f"]}]
        else:
            new = [{"pts": np.vstack([p[:seg_i + 1], proj]),
                    "na": g["na"], "nb": nid, "f": g["f"]},
                   {"pts": np.vstack([proj, p[seg_i + 1:]]),
                    "na": nid, "nb": g["nb"], "f": g["f"]}]
        # by IDENTITY, not equality: `g` is the very object taken from this
        # list, and list.index() would compare dicts holding numpy arrays
        # elementwise -- a DeprecationWarning today and an error in a future
        # numpy, for a lookup that never wanted value semantics.
        i = next(k for k, e in enumerate(edges) if e is g)
        edges[i:i + 1] = new

    changed = True; guard = 0
    while changed and guard < 10000:
        guard += 1; changed = False
        fan = fan_counts()
        for e in list(edges):
            if _is_closed_poly(e["pts"]) and (e["na"] is None or e["na"] == e["nb"]):
                continue                                  # closed curve: no free end
            for je, key in ((0, "na"), (-1, "nb")):
                nid = e[key]
                if nid is not None and fan.get(nid, 0) >= 2:
                    continue                              # already a real shared junction
                P = e["pts"][je]
                best = None
                for g in edges:
                    if g is e:
                        continue
                    d, seg_i, proj, s, tot = _project_point_polyline(P, g["pts"])
                    if d < tol and endmargin < s < (tot - endmargin):
                        if best is None or d < best[0]:
                            best = (d, g, seg_i, proj)
                if best is None:
                    continue
                _, g, seg_i, proj = best
                if nid is None:
                    nid = next_id; next_id += 1
                e["pts"] = e["pts"].copy(); e["pts"][je] = proj.copy()
                e[key] = nid
                split(g, seg_i, proj, nid)
                changed = True
                break
            if changed:
                break

    out = []
    for e in edges:
        g = {**e["f"], "node_a": e["na"], "node_b": e["nb"],
             "points": [[round(float(a), 1), round(float(b), 1)] for a, b in e["pts"]]}
        out.append(g)
    return out



def landmark_boundaries(bundle, tier="landmarks", atlas=None, max_pts=400,
                        outer=True, midline=True):
    """Curated alignment boundaries in plane microns [lr, si]. Interior region
    borders come from the planar ARRANGEMENT of the coarse label map, so every
    shared border between two regions is a SINGLE continuous polyline (never
    drawn twice) and edges that meet at a junction share an identical endpoint
    node (`node_a`/`node_b`) -- the curation UI keeps them glued when dragged.
    Borders that run along the tissue edge are dropped (the whole-tissue outer
    contour, drawn ONCE, covers them); the anatomical midline (atlas ML=0) is
    added separately. Every feature is `editable`. Region classification and the
    label raster are untouched; this only changes how the same set of editable
    boundaries is represented (deduplicated + node-connected)."""
    from skimage import measure
    atlas = atlas or get_atlas()
    acronyms = LANDMARK_TIERS.get(tier, LANDMARK_TIERS["landmarks"])
    idmap, label_acro = _group_by_landmarks(bundle, acronyms, atlas)
    tissue = np.asarray(bundle["AphiL"]) > 0
    feats = []

    def emit_um(um, acro, name, kind, sid=None, parent="", na=None, nb=None):
        um = np.asarray(um, float)
        if len(um) < 2:
            return
        closed = len(um) > 3 and bool(np.allclose(um[0], um[-1]))
        um = _smooth_polyline(um, closed)           # overlay-only curve smoothing
        if len(um) > max_pts:
            keep_last = um[-1].copy()
            um = um[:: int(np.ceil(len(um) / max_pts))]
            if closed and not np.allclose(um[0], um[-1]):
                um = np.vstack([um, um[:1]])
            elif not closed and not np.allclose(um[-1], keep_last):
                um = np.vstack([um, keep_last])     # keep node endpoint exact
        feats.append({"id": (int(sid) if sid else None),
                      "acronym": acro, "name": name, "parent": parent,
                      "kind": kind, "editable": True,
                      "node_a": na, "node_b": nb,
                      "points": [[round(float(a), 1), round(float(b), 1)]
                                 for a, b in um]})

    # (1) ONE whole-tissue outer contour
    if outer:
        for ct in closed_contours(tissue):
            if len(ct) >= 6:
                emit_um(_px_to_um(bundle, ct), "tissue", "Outer contour", "outer")

    # (2) interior region borders as arrangement edges (each shared border once,
    #     junctions connected by shared node ids). Tissue-edge borders dropped.
    def _meta(gid):
        try:
            s = atlas.structures[int(gid)]
            pids = s["structure_id_path"]
            par = (atlas.structures[int(pids[-2])]["acronym"]
                   if len(pids) >= 2 else "")
            return s["name"], par
        except Exception:
            return label_acro.get(gid, str(gid)), ""

    Lc = idmap.astype(np.int64).copy()
    Lc[~tissue] = -1                                # -1 exterior, 0 unselected, >0 region
    node_id = {}                                    # corner -> compact int id
    def nid(corner):
        if corner is None:
            return None
        return node_id.setdefault(corner, len(node_id))
    raw = []
    for e in _trace_arrangement(Lc):
        pr = e["pair"]
        if -1 in pr or max(pr) <= 0:                # skip tissue-edge / non-region
            continue
        if len(e["path"]) < 6:
            continue
        na = e["node_a"]; nb = e["node_b"]
        if na is not None and na == nb:             # degenerate self-loop -> treat closed
            na = nb = None
        raw.append({"pts": _px_to_um(bundle, np.array(e["path"], float) - 1.5),
                    "pair": frozenset(x for x in pr if x > 0),
                    "na": nid(na), "nb": nid(nb)})
    for e in _cleanup_edges(raw):                   # drop artefact fragments, merge splits
        regs = sorted(e["pair"])
        if len(regs) == 2:
            n0, _ = _meta(regs[0]); n1, _ = _meta(regs[1])
            acro = f"{label_acro.get(regs[0], regs[0])}|{label_acro.get(regs[1], regs[1])}"
            emit_um(e["pts"], acro, f"{n0} | {n1}", "structure", sid=None,
                    parent="", na=e["na"], nb=e["nb"])
        elif regs:
            g = regs[0]; nm, par = _meta(g)
            emit_um(e["pts"], label_acro.get(g, str(g)), nm, "structure",
                    sid=g, parent=par, na=e["na"], nb=e["nb"])

    # (3) ONE midline (atlas ML = 0)
    if midline and "tf" in bundle:
        ml = np.asarray(bundle["tf"])[..., 2]
        for ct in measure.find_contours(ml, 0.0):
            if len(ct) >= 6:
                emit_um(_px_to_um(bundle, ct), "midline", "Midline (ML=0)",
                        "midline")

    # build the FULL planar-graph topology once: split the outer contour / midline
    # (and any curve) wherever a region border's dangling endpoint lands on its
    # interior, and give the T-junction a single shared node so drags stay glued.
    feats = _stitch_tjunctions(feats)
    return feats
