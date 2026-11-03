import assert from "node:assert/strict";
import {
  affineMatrix, applyAffine, anchoredScaleFromHandle,
} from "../web/frontend/src/affine.js";

const center = [1000, 600];
const halfW = 500, halfH = 300;
const p0 = { tx: 40, ty: -25, rotDeg: 17, sx: 1.1, sy: 0.9 };

function world(local, p) {
  return applyAffine(
    affineMatrix(center[0], center[1], p.tx, p.ty, p.rotDeg, p.sx, p.sy),
    [center[0] + local[0], center[1] + local[1]],
  );
}

// Pulling the bottom handle farther down keeps the top midpoint exactly fixed.
const top0 = world([0, -halfH], p0);
const bottomTarget = world([0, halfH * 1.5], p0);
const bottom = anchoredScaleFromHandle(
  "y", bottomTarget, center, p0, halfW, halfH, 0, 1,
);
const top1 = world([0, -halfH], bottom);
assert.ok(Math.hypot(top1[0] - top0[0], top1[1] - top0[1]) < 1e-9);
assert.ok(bottom.sy > p0.sy);

// The same invariant holds for a rotated bottom-right corner: opposite corner fixed.
const opposite0 = world([-halfW, -halfH], p0);
const targetCorner = world([halfW * 1.3, halfH * 1.4], p0);
const corner = anchoredScaleFromHandle(
  "both", targetCorner, center, p0, halfW, halfH, 1, 1,
);
const opposite1 = world([-halfW, -halfH], corner);
assert.ok(Math.hypot(
  opposite1[0] - opposite0[0], opposite1[1] - opposite0[1],
) < 1e-9);

console.log("anchored affine handle tests passed");
