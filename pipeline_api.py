#!/usr/bin/env python
"""Callable core of the MERFISH-to-Allen web pipeline.

This module is now a FACADE. The implementation was split out of a single
2439-line file into focused modules under allen_alignment/ on 2026-09-14 so that
each concern can be maintained and extended on its own:

    allen_alignment/plane.py         pixel <-> micron conversions
    allen_alignment/atlas.py         CCF volume, ontology, hierarchy roll-up
    allen_alignment/warp.py          CSRBF kernel, control lattice, validation
    allen_alignment/transforms.py    affine / composite applied to the label raster
    allen_alignment/boundaries.py    overlay contours, labels, fills
    allen_alignment/figures.py       matplotlib rendering
    allen_alignment/registration.py  density -> fitted plane (writes the bundle)
    allen_alignment/finalize.py      labels + deliverables

Every name the application and the validation harnesses already imported is
re-exported here unchanged, so `import pipeline_api as api` keeps working and no
caller had to move. New code should prefer importing the specific module -- for
example `from allen_alignment.warp import validate_warp_grid` -- which makes the
dependency explicit and avoids pulling torch in just to read a constant.

The dependency graph is acyclic, in this order:
    plane, atlas  ->  warp  ->  transforms, boundaries  ->  figures  ->  finalize, registration
"""
from allen_alignment.plane import (  # noqa: F401
    _plane_spacing, _px_to_um,
)
from allen_alignment.atlas import (  # noqa: F401
    RES, COARSE_REGION_DEFAULT_COLOR, COARSE_REGION_STYLE, CURATION_TIER,
    DEPTH2_LEVEL, LANDMARK_TIERS, STRUCTURE_TREE_CSV, ResolutionMismatch,
    atlas_provenance, bundle_res_um, check_bundle_resolution,
    get_atlas, region_style, _ROOT_ID, _ancestor_at_depth, _coarsen_ids,
    _group_by_depth2, _group_by_landmarks, _load_structure_tree,
)
from allen_alignment.warp import (  # noqa: F401
    WARP_EDGE_DISP_FRAC, WARP_GRID_ALGO, WARP_GRID_COLS, WARP_GRID_FINE_COLS,
    WARP_GRID_FINE_ROWS, WARP_GRID_MAX_DIM, WARP_GRID_RADIUS_FACTOR,
    WARP_GRID_ROWS, WARP_INV_MAX_BAD_FRAC, WARP_INV_MAX_ITER,
    WARP_INV_MIN_DET, WARP_INV_TOL, WARP_JAC_EPS, WARP_JAC_MAX, WARP_JAC_MIN,
    WARP_JAC_MIN_BOOSTED, WARP_JAC_PER_CELL, WARP_JAC_SAMPLES,
    WARP_COMPOSED_JAC_MIN, WARP_MAX_DISP_FRAC,
    WARP_MAX_DISP_FRAC_CEILING, WARP_MAX_LAYERS, WARP_NODE_CORNER,
    WARP_NODE_EDGE_H, WARP_NODE_EDGE_V, WARP_NODE_FREE, validate_warp_grid,
    warp_csrbf_invert, warp_csrbf_points, warp_grid_edge_mask,
    warp_grid_extent, warp_grid_nodes, warp_grid_radius, warp_jac_min,
    warp_layers,
    warp_jac_samples, warp_max_disp, warp_movable_count, warp_node_kinds,
    _csrbf_eval, _csrbf_field_and_jac, _csrbf_weights, _warp_jacobian_dets,
    _wendland,
)
from allen_alignment.transforms import (  # noqa: F401
    AFFINE_SCALE_MAX, AFFINE_SCALE_MIN, affine_apply, affine_from_params,
    affine_invert, apply_manual, warp_affine, warp_chain_points, warp_composed_jac,
    warp_composite, warp_composite_layers, warp_csrbf,
    _manual_matrix,
)
from allen_alignment.boundaries import (  # noqa: F401
    ALIGNMENT_GUIDE_SPECS, CURATION_MIN_LABEL_AREA_UM2,
    CURATION_MIN_LOOP_AREA, CURATION_MIN_OPEN_COMP, CURATION_SPUR_MAXLEN,
    CURATION_TJUNC_ENDMARGIN, CURATION_TJUNC_TOL, DEPTH2_COMP_AREA_FRAC,
    DEPTH2_FILL_ALPHA_RESULTS, DEPTH2_MIN_COMP_PX, DEPTH2_MIN_EDGE_FLOOR_UM,
    DEPTH2_MIN_EDGE_FRAC, alignment_region_boundaries, bundle_boundaries,
    depth2_boundaries, depth2_fill_rgba, depth2_region_labels,
    landmark_boundaries, region_labels, _cleanup_edges, _closed_mask_rings,
    _depth2_clean, _hex_to_rgb, _is_closed_poly, _join_edges, _poly_area,
    _poly_len, _project_point_polyline, _rdp, _resample_arclen,
    _smooth_polyline, _stitch_tjunctions, _trace_arrangement,
)
from allen_alignment.figures import (  # noqa: F401
    _curated_result_fig, _leaf_result_color, _plane_dapi, _strip_axes,
    _top_leaf_region_styles,
)
from allen_alignment.registration import (  # noqa: F401
    load_bundle, register_region, render_auto_result, validate_region,
)
from allen_alignment.finalize import (  # noqa: F401
    finalize_composite, finalize_csrbf, finalize_manual, _bundle_geojson,
)
