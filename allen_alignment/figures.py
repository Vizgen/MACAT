"""Matplotlib rendering for the registration preview and the curated result, plus
the result-figure colour palette. Isolated so that changing how a figure looks
never touches the pipeline that produced it."""
import numpy as np
import os
import colorsys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .boundaries import (DEPTH2_FILL_ALPHA_RESULTS, _hex_to_rgb, depth2_boundaries, depth2_fill_rgba, depth2_region_labels)


# Stable, colourblind-conscious high-contrast palette for the most abundant
# ORIGINAL Allen leaf regions in result figures and the composition table.
def _leaf_result_color(rank):
    """Unbounded high-contrast categorical palette using golden-angle hues."""
    hue = (0.58 + rank * 0.618033988749895) % 1.0
    saturation = (0.78, 0.62, 0.88)[rank % 3]
    value = (0.82, 0.95)[(rank // 3) % 2]
    rgb = colorsys.hsv_to_rgb(hue, saturation, value)
    return "#{:02X}{:02X}{:02X}".format(
        *(int(round(channel * 255)) for channel in rgb))



def _top_leaf_region_styles(uids, counts, order, info, limit=None):
    """Return ALL ranked leaf rows plus a stable ``leaf id -> colour`` map."""
    rows = []
    colors = {}
    ranked = order if limit is None else order[:limit]
    for rank, (uid, count) in enumerate(zip(uids[ranked], counts[ranked])):
        color = _leaf_result_color(rank)
        colors[int(uid)] = color
        acro, name, parent, _, _ = info[uid]
        rows.append((acro, name, int(count), parent, color))
    return rows, colors



def _plane_dapi(bundle, supersample=2):
    """Plane-aligned DAPI for the result figure, or None.

    Layering note: the resample lives in web/previews.py (it is also served to the
    curation canvas as /api/dapi.png) and previews imports THIS module, so the import
    has to be local and guarded -- pipeline_api must keep working with no web/ around
    (CLI finalize, tests). A missing DAPI simply drops the panel."""
    try:
        from web import previews as _pv
        return _pv.dapi_on_plane(bundle, supersample=supersample)
    except Exception:
        return None



def plane_density(bundle):
    """The bundle's cell-density image (Jimg), auto-contrasted to [0, 1]: clipped
    to its 1st-99.5th percentiles so a few bright cells do not wash out the
    tissue. The background wherever DAPI is missing -- the curation canvas
    (/api/density.png, drawn in grayscale) and the result figure -- so both show
    the same image. None if the bundle has no Jimg."""
    if "Jimg" not in bundle:
        return None
    J = np.asarray(bundle["Jimg"], np.float64)
    finite = J[np.isfinite(J)]
    lo, hi = np.percentile(finite, [1.0, 99.5]) if finite.size else (0.0, 1.0)
    if hi <= lo:
        lo, hi = float(finite.min()), float(finite.max() or 1.0)
    return np.clip((J - lo) / max(hi - lo, 1e-12), 0.0, 1.0)


def _strip_axes(ax):
    """No ticks, no labels, no frame (7.28 §一.4 / item 11: result figures should
    carry none of it). Limits and aspect are set by the caller."""
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlabel(""); ax.set_ylabel("")



# Overlay depth for the curated result figure. Depth 2 (CH / BS / lfbs / VL) is
# what this figure used through 2026-09-21; depth 4 names the structures a
# reviewer is actually checking. `depth2_boundaries(level=...)` is the same
# generator the curation overlay uses, so the two speak one language.
RESULT_OVERLAY_DEPTH = 4

# Fixed hierarchy depths for the per-cell panels, alongside the leaf view.
#
# Switched OFF at the user's request (2026-09-21) after review: the rolled-up
# panels were judged distracting and to add little over the leaf views, since the
# depth-4 overlay above already shows the same partition drawn as regions rather
# than as cells. Emptied rather than deleted -- `_rollup`, the per-panel note and
# the layout all still handle them, so restoring the tuple below is the whole
# change.
#
# Fixed depths rather than "N levels up", if they ever come back: leaves sit
# anywhere from depth 0 to 9, so a relative roll-up gives a ragged set spanning
# many depths rather than a partition.
# CELL_PANEL_DEPTHS = (2, 3, 4)
CELL_PANEL_DEPTHS = ()

RESULT_DPI = 300

# Cells with no region at the depth being drawn. A much lighter tint than the
# original (0.62, 0.62, 0.62, 0.22), because Allen's own palette is full of greys
# and the two were competing.
#
# Measured, composited on the white figure background:
#
#     fiber tracts (#CCCCCC at alpha 0.9)   luminance 0.820
#     VL / V3      (#AAAAAA at alpha 0.9)             0.700
#     unmapped, before                                0.916   gap 0.096
#     unmapped, now                                   0.976   gap 0.156
#
# So this is not "make the grey lighter so it reads as background" -- unmapped was
# always the lighter of the two. It is widening a gap that was too small to see
# against structures which are themselves achromatic.
#
# The trade-off, and it is real: at deeper panels the unmapped population is large
# and spatially structured -- fiber tracts have no depth-4 ancestor, so at depth 4
# they ARE the unmapped cells. Pushing this much lighter makes that population
# faint. Each panel states the count in its corner precisely because the picture
# now under-sells it.
UNMAPPED_RGBA = (0.80, 0.80, 0.80, 0.12)


def _tree_colors():
    """Allen id -> (#rrggbb, acronym, structure_id_path). Cached by atlas.py."""
    from .atlas import _load_structure_tree
    return _load_structure_tree()


def _rollup(rid, depth, tree):
    """Per-cell ancestor id at a literal CSV depth, 0 where there is none.

    `_ancestor_at_depth` deliberately does not clamp, so a structure shallower
    than `depth` has no ancestor there and is reported unmapped rather than being
    faked up to its own id. That is a real population -- 2.7% of cells at depth 2
    rising to 18.8% at depth 5 on a measured job -- so the panels state it.
    """
    from .atlas import _ancestor_at_depth
    out = np.zeros_like(rid)
    for leaf in np.unique(rid[rid > 0]):
        info = tree.get(int(leaf))
        if not info or not info.get("path"):
            continue
        anc = _ancestor_at_depth(info["path"], depth)
        if anc is not None:
            out[rid == leaf] = int(anc)
    return out


def _cell_colors(ids, tree, fallback=None, palette=None):
    """RGBA per cell from the ALLEN colour of each id, grey where unmapped.

    Allen colours instead of the rank palette: at 9-24 regions they make the
    panel directly comparable to the curation overlay and to the composition
    table. `fallback` supplies a colour for ids the tree does not know.
    """
    colr = np.tile(UNMAPPED_RGBA, (len(ids), 1))
    for uid in np.unique(ids[ids > 0]):
        if palette is not None:
            # `palette` wins outright. Allen's own colours are anatomically
            # meaningful but poorly separated at leaf level -- 173 regions drawn
            # from a scheme built for nested shading -- so the rank palette is
            # kept as a parallel view rather than replaced by it.
            hexcol = palette.get(int(uid))
        else:
            info = tree.get(int(uid))
            hexcol = (info or {}).get("color") or (fallback or {}).get(int(uid))
        if not hexcol:
            continue
        colr[ids == uid] = list(np.asarray(_hex_to_rgb(hexcol), float) / 255.0) + [0.9]
    return colr


# Legend rows under each cell panel. Fixed at 4 so every cell panel reserves the
# same vertical space and therefore comes out the same height -- a legend that
# grew with the number of regions made the panels ragged.
LEGEND_ROWS = 4


def _legend_rows(ids, tree, limit=16, palette=None):
    """[(acronym, count, colour)] for the most abundant ids, largest first."""
    uid, cnt = np.unique(ids[ids > 0], return_counts=True)
    rows = []
    for u, c in sorted(zip(uid, cnt), key=lambda t: -t[1])[:limit]:
        info = tree.get(int(u)) or {}
        color = (palette or {}).get(int(u)) or info.get("color", "#8aa0b8")
        rows.append((info.get("acronym", str(int(u))), int(c), color))
    return rows


def _curated_result_fig(bundle, A2, rid, out_dir, tag, atlas,
                        top_regions, leaf_colors, prefix="curated",
                        suptitle=None):
    """FINAL curated figure. Returns {"png", "zip", "panels"}.

    Six panels, all sharing ONE frame (centred plane microns, identical extent
    and orientation) so they are directly comparable, none with ticks or a frame:

        depth-4 regions over DAPI       depth-4 regions over marker genes
        cells by leaf Allen region      cells rolled up to depth 2
        cells rolled up to depth 3      cells rolled up to depth 4

    Each panel is ALSO written on its own at full width, and the two overlay
    panels additionally as SVG -- their boundaries are polygons, so a vector copy
    stays sharp at any zoom where a raster does not. Everything is collected into
    one zip so Results needs a single download.
    """
    import zipfile
    import matplotlib.patheffects as _pe
    from matplotlib.lines import Line2D

    lm_bundle = {
        "AphiL": np.asarray(A2, np.int32),
        "xJ0": np.asarray(bundle["xJ0"], np.float32),
        "xJ1": np.asarray(bundle["xJ1"], np.float32),
        "id2acro": bundle["id2acro"], "id2name": bundle["id2name"],
    }
    d = RESULT_OVERLAY_DEPTH
    lm_feats = depth2_boundaries(lm_bundle, atlas=atlas, level=d)
    labels = depth2_region_labels(lm_bundle, level=d)
    fill = depth2_fill_rgba(lm_bundle, DEPTH2_FILL_ALPHA_RESULTS, level=d)
    xJ0 = np.asarray(bundle["xJ0"], float); xJ1 = np.asarray(bundle["xJ1"], float)
    ext = [xJ1[0], xJ1[-1], xJ0[-1], xJ0[0]]
    tree = _tree_colors()

    def frame(ax):
        ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
        ax.set_aspect("equal")
        _strip_axes(ax)

    def draw_overlay(ax, img, cmap, title, rgba=False):
        if img is None:
            frame(ax)
            ax.text(0.5, 0.5, "background not available\nfor this experiment",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=10, color="0.5")
        else:
            if rgba:
                ax.imshow(img, extent=ext, aspect="equal", interpolation="nearest")
            else:
                ax.imshow(img, cmap=cmap, extent=ext, aspect="equal")
            ax.imshow(fill, extent=ext, aspect="equal", interpolation="nearest")
            for f in lm_feats:
                for ring in (f.get("rings") or [{"points": f.get("points", [])}]):
                    pts = np.asarray(ring["points"], float)
                    if len(pts) < 2:
                        continue
                    ax.plot(pts[:, 0], pts[:, 1], color=f.get("color", "cyan"),
                            linewidth=0.7, alpha=0.85, solid_capstyle="round",
                            linestyle="--" if f["kind"] == "midline" else "-")
            for lab in labels:
                ax.text(lab["lr"], lab["si"], lab["short"], color=lab["color"],
                        fontsize=8, fontweight="bold", ha="center", va="center",
                        alpha=0.95,
                        path_effects=[_pe.withStroke(linewidth=1.6,
                                                     foreground="black")])
            frame(ax)
        ax.set_title(title, fontsize=11)

    # xr/yr are ORIENTED microns; the overlays are CENTRED plane microns
    # (xJ1 = X - X.mean()), so shift by the same offsets the grid used.
    px = np.asarray(bundle["xr"], float) - (float(bundle["c0"]) - ext[0])
    py = np.asarray(bundle["yr"], float) - (float(bundle["r0"]) - ext[3])
    n_lab = int((rid > 0).sum())

    def draw_cells(ax, ids, title, note=None, palette=None):
        ax.scatter(px, py, s=1.2,
                   c=_cell_colors(ids, tree, leaf_colors, palette=palette),
                   linewidths=0)
        frame(ax)
        ax.set_title(title, fontsize=11)
        rows = _legend_rows(ids, tree, palette=palette)
        if rows:
            handles = [Line2D([], [], marker="o", linestyle="none", markersize=5,
                              markerfacecolor=c, markeredgecolor="none",
                              label=f"{a}  ({n:,})") for a, n, c in rows]
            # Below the projection, not over it: at 16 entries the old
            # upper-right legend sat on the cortex. Padded to a full grid so the
            # block is always LEGEND_ROWS tall whether a panel has 9 regions or
            # 16, which is what keeps the panels the same height.
            ncol = int(np.ceil(len(handles) / LEGEND_ROWS))
            while len(handles) < ncol * LEGEND_ROWS:
                handles.append(Line2D([], [], linestyle="none", marker="none",
                                      label=" "))
            ax.legend(handles=handles, loc="upper center",
                      bbox_to_anchor=(0.5, -0.02), ncol=ncol, frameon=False,
                      fontsize=7, labelspacing=0.3, handletextpad=0.4,
                      columnspacing=1.2)
        if note:
            # Inside the axes, top-left. Below the panel it collided with the
            # legend, whose height now varies with the number of columns; the
            # corner above the section is empty on every panel.
            ax.text(0.01, 0.99, note, transform=ax.transAxes, ha="left",
                    va="top", fontsize=7, color="0.45")

    # The marker-gene background that replaced cell density. Optional by nature:
    # it needs cell_by_gene.csv and enough markers for at least one division, so
    # a panel that cannot be built says so rather than failing the whole figure.
    marker_rgba = None
    try:
        from . import markers as _markers
        exp_path = str(bundle["exp_path"]) if "exp_path" in bundle else ""
        _planes, _report = _markers.region_planes(bundle, exp_path)
        marker_rgba = _markers.composite_rgba(_planes, _report)
    except Exception:
        marker_rgba = None

    dapi = _plane_dapi(bundle)
    # No DAPI (e.g. the Allen Brain Cell Atlas sections, which ship cell tables
    # only): fall back to cell density, the background the curation canvas
    # showed, rather than an empty panel. The panel keeps its file name.
    bg_name = "DAPI"
    if dapi is None:
        dapi = plane_density(bundle)
        bg_name = "cell density (no DAPI)"

    def _note(ids):
        lost = int(((rid > 0) & (ids == 0)).sum())
        return (None if not lost else
                f"{lost:,} of {n_lab:,} labelled cells "
                f"({100 * lost / max(n_lab, 1):.1f}%) have no ancestor at this depth")

    panels = [
        ("regions_over_dapi",
         lambda ax: draw_overlay(ax, dapi, "gray", f"depth-{d} regions over {bg_name}")),
        ("regions_over_markers",
         lambda ax: draw_overlay(ax, marker_rgba, None,
                                 f"depth-{d} regions over marker genes", rgba=True)),
        ("cells_leaf_rank",
         lambda ax: draw_cells(ax, rid,
                               "cells by Allen region (leaf level; rank palette)",
                               palette=leaf_colors)),
        ("cells_leaf_allen",
         lambda ax: draw_cells(ax, rid,
                               "cells by Allen region (leaf level; Allen palette)")),
    ]
    for depth in CELL_PANEL_DEPTHS:
        ids = _rollup(rid, depth, tree)
        panels.append((f"cells_depth{depth}",
                       lambda ax, i=ids, dd=depth: draw_cells(
                           ax, i, f"cells by Allen region (depth {dd})", _note(i))))

    _pw = 7.0
    _ph = _pw * abs(ext[2] - ext[3]) / max(abs(ext[1] - ext[0]), 1e-9)
    # Rows of 2, 2 and 3. A 6-column grid divides evenly by both, so a row of two
    # takes three columns each and a row of three takes two, without either row
    # having to know about the other. Row heights are equal, so the third row's
    # panels are narrower and their content correspondingly smaller -- the cost of
    # asking for 2|2|3 rather than a uniform grid.
    ROWS = [["regions_over_dapi", "regions_over_markers"],
            ["cells_leaf_rank", "cells_leaf_allen"],
            [f"cells_depth{d_}" for d_ in CELL_PANEL_DEPTHS]]
    # a row can be empty when a panel group is switched off; dividing the grid
    # span by len(row) would otherwise raise
    ROWS = [r for r in ROWS if r]
    by_name = dict(panels)
    # anything a row does not claim still gets drawn, appended as its own row
    claimed = {n for row in ROWS for n in row}
    extra = [n for n, _ in panels if n not in claimed]
    if extra:
        ROWS.append(extra)
    SPAN = 6
    fig = plt.figure(figsize=(2 * _pw, len(ROWS) * (_ph + 1.15) + 0.6))
    gs = fig.add_gridspec(len(ROWS), SPAN)
    for r, row in enumerate(ROWS):
        width = SPAN // len(row)
        for c, name in enumerate(row):
            draw = by_name.get(name)
            if draw is None:
                continue
            draw(fig.add_subplot(gs[r, c * width:(c + 1) * width]))
    fig.suptitle(suptitle or
                 f"final curated result | {(rid > 0).mean() * 100:.1f}% labeled")
    fig.tight_layout()
    # `prefix` keeps the historical filenames: "curated" for the confirmed result,
    # "register" for the automatic preview, so neither overwrites the other and
    # nothing downstream has to learn a new name.
    out = os.path.join(out_dir, f"{prefix}_result{tag}.png")
    # bbox_inches="tight" because the legends sit OUTSIDE their axes now:
    # tight_layout reserves space for axes content, not for an artist anchored
    # below one, so without this the bottom row's legend is cropped away.
    fig.savefig(out, dpi=RESULT_DPI, bbox_inches="tight"); plt.close(fig)

    written = []
    for name, draw in panels:
        one, ax = plt.subplots(figsize=(_pw * 1.6, _ph * 1.6 + 0.6))
        draw(ax)
        one.tight_layout()
        base = os.path.join(out_dir, f"{prefix}_{name}{tag}")
        one.savefig(base + ".png", dpi=RESULT_DPI, bbox_inches="tight")
        written.append(base + ".png")
        if name.startswith("regions_over_"):
            # Vector for the overlay panels: the boundaries and acronyms stay
            # sharp at any zoom. The background stays a raster inside the SVG --
            # DAPI and the marker composite are images, and vectorising them
            # would invent detail that is not in the data.
            # dpi matters for an SVG too. The boundaries and labels are vector
            # and resolution-independent, but the DAPI / marker background is a
            # raster EMBEDDED in the file, and its resolution follows dpi.
            # Without this it was written at matplotlib's default 100 dpi --
            # 1090x620 against the PNG's 3330x1978 -- so the vector copy had a
            # visibly coarser background than the raster one it was meant to beat.
            one.savefig(base + ".svg", dpi=RESULT_DPI, bbox_inches="tight")
            written.append(base + ".svg")
        plt.close(one)

    zip_path = os.path.join(out_dir, f"{prefix}_result{tag}_panels.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(out, os.path.basename(out))
        for f in written:
            z.write(f, os.path.basename(f))
    return {"png": out, "zip": zip_path, "panels": written}
