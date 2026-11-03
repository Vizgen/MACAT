// Behaviour tests for the Results overlay placement (web/frontend/src/overlayPlace.js).
//
// Run: node tests/test_overlay_ui.mjs
//
// What makes this worth testing on its own: Results has to REBUILD the curated
// placement, because no curated raster is ever saved. The bundle holds the
// AUTOMATIC label plane; "curated" is that plane plus a confirmed affine and
// CSRBF lattice. Every way of getting that wrong produces a picture that is
// plausible rather than broken -- boundaries sitting a few hundred microns off
// the anatomy they claim to bound -- so it has to be pinned numerically.
//
// The three failure modes below are the ones that survive a glance:
//   1. composing warp-then-affine instead of affine-then-warp;
//   2. fitting the view to the transformed regions instead of the bundle plane,
//      which slides the background image against the overlay by exactly however
//      far curation moved things;
//   3. a division toggle that loses legend order or never returns to "all".
import assert from "node:assert/strict";
import {
  buildPlace, planeExtent, fitView, overlayLegend, toggleDivision,
} from "../web/frontend/src/overlayPlace.js";
import { affineMatrix, applyAffine } from "../web/frontend/src/affine.js";
import {
  gridNodes, gridRadius, buildWarp, warpWeights, warpPoints,
} from "../web/frontend/src/warp.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };
const close = (a, b, tol = 1e-9) => Math.abs(a - b) <= tol;

const extent = [-5825, 5825, -3425, 3425];   // a real bundle's plane, microns
const meta = { lr0: extent[0], lr1: extent[1], si0: extent[2], si1: extent[3] };
const AFF = { cx: 0, cy: 0, tx: 120, ty: -80, rot_deg: 7, sx: 1.05, sy: 0.96 };

function warpLattice(rows = 5, cols = 5, push = 300) {
  const src = gridNodes(extent, rows, cols);
  const R = gridRadius(extent, rows, cols);
  const dst = src.map((p) => [p[0], p[1]]);
  const mid = Math.floor((rows * cols) / 2);
  dst[mid] = [src[mid][0] + push, src[mid][1] - push];
  return { algo: "csrbf_wendland_c2", rows, cols, R, src, dst };
}

test("a null transform is the identity -- the automatic alignment", () => {
  const place = buildPlace(null);
  for (const p of [[0, 0], [-1234.5, 987.25], [5825, -3425]]) {
    const q = place(p);
    assert.ok(close(q[0], p[0]) && close(q[1], p[1]),
              `identity moved ${p} to ${q}`);
  }
  // and it must not hand back the caller's own array, which a draw loop would
  // then be free to mutate
  const src = [1, 2];
  assert.notStrictEqual(buildPlace(null)(src), src);
  assert.notStrictEqual(buildPlace({})(src), src);
});

test("affine only matches applyAffine exactly", () => {
  const place = buildPlace({ affine: AFF });
  const M = affineMatrix(AFF.cx, AFF.cy, AFF.tx, AFF.ty, AFF.rot_deg, AFF.sx, AFF.sy);
  for (const p of [[0, 0], [-4000, 2500], [3333, -1111]]) {
    const q = place(p), e = applyAffine(M, p);
    assert.ok(close(q[0], e[0], 1e-9) && close(q[1], e[1], 1e-9));
  }
});

test("the composition is affine FIRST, then the local warp", () => {
  const warp = warpLattice();
  const place = buildPlace({ affine: AFF, warp });
  const M = affineMatrix(AFF.cx, AFF.cy, AFF.tx, AFF.ty, AFF.rot_deg, AFF.sx, AFF.sy);
  const fac = buildWarp(warp.src, warp.R);
  const weights = warpWeights(fac, warp.dst);

  // Corner and edge nodes are pinned, so out there the field is ~zero and the
  // two orders agree however they are composed. These are interior points, where
  // the field is live and the orders genuinely separate.
  const interior = [[0, 0], [-2000, 1500], [800, -2200]];
  for (const p of [...interior, [4500, 2800]]) {
    const got = place(p);
    const right = warpPoints(fac, weights, [applyAffine(M, p)])[0];   // affine then warp
    assert.ok(close(got[0], right[0], 1e-9) && close(got[1], right[1], 1e-9),
              `composition does not match curation at ${p}`);
  }
  for (const p of interior) {
    const right = warpPoints(fac, weights, [applyAffine(M, p)])[0];
    const wrong = applyAffine(M, warpPoints(fac, weights, [p])[0]);   // the plausible slip
    assert.ok(Math.hypot(right[0] - wrong[0], right[1] - wrong[1]) > 1,
              `the two orders agree at ${p}; this point proves nothing`);
  }
});

test("an empty lattice falls back to the affine rather than throwing", () => {
  const M = affineMatrix(AFF.cx, AFF.cy, AFF.tx, AFF.ty, AFF.rot_deg, AFF.sx, AFF.sy);
  for (const w of [null, undefined, {}, { src: [] }, { src: null, R: 1 }]) {
    const q = buildPlace({ affine: AFF, warp: w })([1000, -500]);
    const e = applyAffine(M, [1000, -500]);
    assert.ok(close(q[0], e[0]) && close(q[1], e[1]), `warp=${JSON.stringify(w)}`);
  }
});

test("the view frame is the bundle plane, not the transformed regions", () => {
  const e = planeExtent(meta);
  assert.deepEqual(e, { x0: -5825, x1: 5825, y0: -3425, y1: 3425 });
  assert.equal(planeExtent(null), null);
  assert.equal(planeExtent({ lr1: 1 }), null);

  // A curated transform moves the regions well outside the plane. The extent
  // must not follow them: the background image covers the plane, so a frame
  // fitted to the regions would slide the image against the overlay.
  const place = buildPlace({ affine: { ...AFF, tx: 2000, ty: 1500 } });
  const moved = place([meta.lr1, meta.si1]);
  assert.ok(moved[0] > meta.lr1, "the test transform should push past the plane");
  assert.deepEqual(planeExtent(meta), e, "extent must not depend on the transform");
});

test("fitView centres the plane and maps its corners inside the canvas", () => {
  const W = 800, H = 400;
  const v = fitView(planeExtent(meta), W, H);
  const at = (lr, si) => [v.tx + lr * v.scale, v.ty + si * v.scale];
  const [ax, ay] = at(meta.lr0, meta.si0);
  const [bx, by] = at(meta.lr1, meta.si1);
  assert.ok(ax >= 0 && ay >= 0 && bx <= W && by <= H, "plane is clipped by the frame");
  assert.ok(close((ax + bx) / 2, W / 2, 1e-6), "not centred horizontally");
  assert.ok(close((ay + by) / 2, H / 2, 1e-6), "not centred vertically");
  // one scale for both axes, or the tissue is stretched
  const aspectIn = (meta.lr1 - meta.lr0) / (meta.si1 - meta.si0);
  assert.ok(close((bx - ax) / (by - ay), aspectIn, 1e-6), "aspect ratio distorted");
  for (const bad of [[null, W, H], [planeExtent(meta), 0, H], [planeExtent(meta), W, 0]]) {
    assert.equal(fitView(...bad), null);
  }
});

test("the legend is one entry per region, sorted, regions only", () => {
  const boundaries = { features: [
    { kind: "outer", acronym: "tissue", name: "Outer contour", color: "#fff" },
    { kind: "midline", acronym: "midline", name: "Midline", color: "#fff" },
    { kind: "region", acronym: "int", name: "internal capsule", color: "#CCC", component: 1 },
    { kind: "region", acronym: "int", name: "internal capsule", color: "#CCC", component: 2 },
    { kind: "region", acronym: "CA1", name: "Field CA1", color: "#7ED04B" },
  ] };
  const leg = overlayLegend(boundaries);
  assert.deepEqual(leg.map((r) => r.acronym), ["CA1", "int"]);
  assert.equal(leg[1].name, "internal capsule");
  assert.deepEqual(overlayLegend(null), []);
  assert.deepEqual(overlayLegend({ features: [] }), []);
});

test("a division toggle keeps legend order and returns to all", () => {
  const all = ["Isocortex", "HPF", "TH", "HY"];
  let on = toggleDivision(all, null, "TH");            // from "all", drop one
  assert.deepEqual(on, ["Isocortex", "HPF", "HY"]);
  on = toggleDivision(all, on, "Isocortex");
  assert.deepEqual(on, ["HPF", "HY"]);
  on = toggleDivision(all, on, "Isocortex");           // back on, in legend order
  assert.deepEqual(on, ["Isocortex", "HPF", "HY"]);
  on = toggleDivision(all, on, "TH");                  // everything on again
  assert.equal(on, null, "a full selection must collapse back to null");
  // all of them off is a real state, not a silent reset to all
  let off = all.reduce((acc, a) => toggleDivision(all, acc, a), null);
  assert.deepEqual(off, []);
});

console.log(`\n${passed} passed`);
