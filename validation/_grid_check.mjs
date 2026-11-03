// Cross-language warp-GRID parity harness (Phase 2). Reads a JSON payload
//   {extent:[lr0,lr1,si0,si1], cases:[{dst:[[lr,si],...], pts:[[lr,si],...]}, ...]}
// from the file given as argv[2], runs the frontend grid module (the exact math the
// Manual Curation canvas uses) and prints
//   {consts, nodes, R, edge, results:[{out, valid}]}
// so validation/_grid_parity.py can compare it to pipeline_api's
// warp_grid_nodes / warp_grid_radius / warp_grid_edge_mask / warp_csrbf_points /
// validate_warp_grid.
import { readFileSync } from "node:fs";
import {
  GRID_ALGO, GRID_ROWS, GRID_COLS, GRID_RADIUS_FACTOR, MAX_DISP_FRAC,
  MAX_DISP_FRAC_CEILING, maxDisp, JAC_MIN_BOOSTED, jacMin,
  JAC_MIN, JAC_MAX, JAC_SAMPLES, JAC_PER_CELL, JAC_EPS, jacSamples,
  EDGE_DISP_FRAC, NODE_CORNER, NODE_EDGE_V, NODE_EDGE_H, NODE_FREE,
  gridNodes, gridRadius, edgeMask, nodeKinds, movableCount,
  forwardWarp, validateGrid,
} from "../web/frontend/src/warp.js";
import { affineMatrix, applyAffine } from "../web/frontend/src/affine.js";

const payload = JSON.parse(readFileSync(process.argv[2], "utf8"));
const extent = payload.extent;
const nodes = gridNodes(extent);
const R = gridRadius(extent);

const results = payload.cases.map((c) => {
  const out = {
    out: forwardWarp(nodes, c.dst, R, c.pts || []),
    valid: validateGrid(nodes, c.dst, R, extent),
  };
  // `placed` is CurationStep's place(): the GLOBAL AFFINE first, then the local
  // forward CSRBF — the exact composition the canvas draws boundaries/labels with.
  if (c.affine) {
    const a = c.affine;
    const M = affineMatrix(a.cx, a.cy, a.tx, a.ty, a.rot_deg, a.sx, a.sy);
    const affPts = (c.pts || []).map((p) => applyAffine(M, p));
    out.placed = forwardWarp(nodes, c.dst, R, affPts);
  }
  return out;
});

process.stdout.write(JSON.stringify({
  consts: {
    algo: GRID_ALGO, rows: GRID_ROWS, cols: GRID_COLS,
    radius_factor: GRID_RADIUS_FACTOR, max_disp_frac: MAX_DISP_FRAC,
    // per-warp displacement budget: the ceiling, plus the resolved cap in
    // microns at the default, at the ceiling, and for a request above it (which
    // must clamp). Both sides must agree or a warp accepted in the browser
    // could be refused at finalize.
    max_disp_frac_ceiling: MAX_DISP_FRAC_CEILING,
    max_disp_default: maxDisp(extent, GRID_ROWS, GRID_COLS, null),
    max_disp_ceiling: maxDisp(extent, GRID_ROWS, GRID_COLS, MAX_DISP_FRAC_CEILING),
    max_disp_clamped: maxDisp(extent, GRID_ROWS, GRID_COLS, 999.0),
    // the fold floor a raised budget opts into, and the resolver both sides use
    jac_min_boosted: JAC_MIN_BOOSTED,
    jac_min_default: jacMin(null),
    jac_min_raised: jacMin(MAX_DISP_FRAC_CEILING),
    jac_min: JAC_MIN, jac_max: JAC_MAX, jac_samples: JAC_SAMPLES,
    jac_per_cell: JAC_PER_CELL, jac_eps: JAC_EPS,
    // the SCALED sample count actually used for this grid
    jac_n: jacSamples(GRID_ROWS, GRID_COLS),
    jac_n_5x5: jacSamples(5, 5),
    edge_disp_frac: EDGE_DISP_FRAC,
    node_corner: NODE_CORNER, node_edge_v: NODE_EDGE_V,
    node_edge_h: NODE_EDGE_H, node_free: NODE_FREE,
    movable: movableCount(GRID_ROWS, GRID_COLS),
  },
  nodes, R, edge: edgeMask(), kinds: nodeKinds(), results,
}));
