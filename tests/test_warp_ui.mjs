import assert from "node:assert/strict";
import {
  GRID_ROWS,
  GRID_COLS,
  FINE_GRID_ROWS,
  FINE_GRID_COLS,
  GRID_PRESETS,
  MAX_GRID_DIM,
  NODE_CORNER,
  NODE_EDGE_V,
  NODE_FREE,
  clampNodeDisplacement,
  gridNodes,
  gridRadius,
  forwardWarp,
  directDragGrid,
  inverseWarpPoint,
  landmarksToGrid,
  nodeDisplacementLimit,
  nodeKinds,
  jacSamples,
  validateGrid,
} from "../web/frontend/src/warp.js";

const extent = [0, 6000, 0, 3000];
const src = gridNodes(extent);
const kinds = nodeKinds();

const topEdge = kinds.findIndex((kind) => kind === NODE_EDGE_V);
const interior = kinds.findIndex((kind) => kind === NODE_FREE);
const corner = kinds.findIndex((kind) => kind === NODE_CORNER);

const edgeLimit = nodeDisplacementLimit(
  extent, kinds[topEdge], GRID_ROWS, GRID_COLS,
);
const interiorLimit = nodeDisplacementLimit(
  extent, kinds[interior], GRID_ROWS, GRID_COLS,
);
assert.ok(edgeLimit > 0);
assert.ok(edgeLimit < interiorLimit);

// A large pointer overshoot on a constrained edge is projected to its legal
// axis and capped, instead of being rejected after pointer-up.
const edgeProposed = [src[topEdge][0] + 1000, src[topEdge][1] + 10000];
const edgeClamped = clampNodeDisplacement(
  kinds[topEdge], src[topEdge], edgeProposed, edgeLimit,
);
assert.equal(edgeClamped[0], src[topEdge][0]);
assert.ok(Math.abs(edgeClamped[1] - src[topEdge][1] - edgeLimit) < 1e-9);

const dst = src.map((p) => [...p]);
dst[topEdge] = edgeClamped;
const verdict = validateGrid(
  src, dst, gridRadius(extent), extent, GRID_ROWS, GRID_COLS,
);
assert.equal(verdict.ok, true, verdict.reason);

// Corners remain immovable even when the pointer overshoots.
assert.deepEqual(
  clampNodeDisplacement(kinds[corner], src[corner], [9999, 9999], edgeLimit),
  src[corner],
);

// The lightweight point-pair authoring layer round-trips clicks on an already
// warped overlay and emits the same validated regular-grid format as manual nodes.
const identity = src.map((p) => [...p]);
const pairGrid = landmarksToGrid(
  src,
  [
    { atlas: [2500, 1200], tissue: [2600, 1250] },
    { atlas: [3500, 1200], tissue: [3400, 1250] },
    { atlas: [3000, 2000], tissue: [3000, 1900] },
  ],
  gridRadius(extent),
  extent,
  GRID_ROWS,
  GRID_COLS,
);
const pairVerdict = validateGrid(
  src, pairGrid, gridRadius(extent), extent, GRID_ROWS, GRID_COLS,
);
assert.equal(pairVerdict.ok, true, pairVerdict.reason);
const probe = [2800, 1500];
const warpedProbe = forwardWarp(src, pairGrid, gridRadius(extent), [probe])[0];
const recoveredProbe = inverseWarpPoint(
  src, pairGrid, gridRadius(extent), warpedProbe,
);
assert.ok(Math.hypot(
  recoveredProbe[0] - probe[0], recoveredProbe[1] - probe[1],
) < 0.1);

// Direct line dragging moves a local neighbourhood, keeps distant points less
// affected, and still emits a grid accepted by the backend-equivalent validator.
const directGrid = directDragGrid(
  src, identity, [3000, 1500], [120, 80], 700,
  extent, GRID_ROWS, GRID_COLS,
);
const directVerdict = validateGrid(
  src, directGrid, gridRadius(extent), extent, GRID_ROWS, GRID_COLS,
);
assert.equal(directVerdict.ok, true, directVerdict.reason);
assert.ok(directVerdict.nMoved > 0);
assert.deepEqual(directGrid[corner], src[corner]);

// Fine mode is a real 13x13 lattice with 121 free interior nodes and validation
// density scaled to the smaller cells. A legal granular pull remains valid.
const fineSrc = gridNodes(extent, FINE_GRID_ROWS, FINE_GRID_COLS);
const fineKinds = nodeKinds(FINE_GRID_ROWS, FINE_GRID_COLS);
assert.equal(fineSrc.length, 169);
assert.equal(fineKinds.filter((kind) => kind === NODE_FREE).length, 121);
assert.equal(jacSamples(FINE_GRID_ROWS, FINE_GRID_COLS), 61);
const fineInterior = fineKinds.findIndex((kind) => kind === NODE_FREE);
const fineDst = fineSrc.map((p) => [...p]);
const fineLimit = nodeDisplacementLimit(
  extent, NODE_FREE, FINE_GRID_ROWS, FINE_GRID_COLS,
);
fineDst[fineInterior][0] += fineLimit * 0.25;
const fineVerdict = validateGrid(
  fineSrc, fineDst, gridRadius(extent, FINE_GRID_ROWS, FINE_GRID_COLS),
  extent, FINE_GRID_ROWS, FINE_GRID_COLS,
);
assert.equal(fineVerdict.ok, true, fineVerdict.reason);

// Every density offered in the sidebar is a lattice the validator accepts, with
// a legal pull on a free node (4x4 and 7x7 dropped, 2026-11-03).
assert.deepEqual(GRID_PRESETS.map((g) => g.rows), [13, 9, 5]);
for (const { rows, cols } of GRID_PRESETS) {
  assert.ok(rows <= MAX_GRID_DIM && cols <= MAX_GRID_DIM);
  const s = gridNodes(extent, rows, cols);
  const k = nodeKinds(rows, cols);
  const d = s.map((p) => [...p]);
  const i = k.findIndex((kind) => kind === NODE_FREE);
  d[i][0] += nodeDisplacementLimit(extent, NODE_FREE, rows, cols) * 0.5;
  const v = validateGrid(s, d, gridRadius(extent, rows, cols), extent, rows, cols);
  assert.equal(v.ok, true, `${rows}x${cols}: ${v.reason}`);
}

const unsupported = validateGrid([], [], 1, extent, 16, 16);
assert.equal(unsupported.ok, false);
assert.equal(unsupported.reason, "unsupported grid dimensions");

console.log("warp UI clamp tests passed");
