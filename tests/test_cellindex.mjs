// Behaviour tests for the per-cell spatial index (web/frontend/src/cellindex.js).
//
// Run: node tests/test_cellindex.mjs
//
// Hovering has to name a cell on every mouse move over ~85,000 points. The index
// is what makes that cheap, and the ways it goes wrong are silent: a wrong
// nearest is still a plausible region name, and a missing early-exit is a hover
// that works but janks.
import assert from "node:assert/strict";
import { buildIndex, nearest } from "../web/frontend/src/cellindex.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };

function grid(n) {                       // n x n points over the uint16 range
  const x = new Uint16Array(n * n), y = new Uint16Array(n * n);
  let k = 0;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    x[k] = Math.round((i / (n - 1)) * 65535);
    y[k] = Math.round((j / (n - 1)) * 65535);
    k++;
  }
  return { x, y };
}

const brute = (x, y, qx, qy, vis) => {
  let best = -1, bd = Infinity;
  for (let i = 0; i < x.length; i++) {
    if (vis && !vis(i)) continue;
    const d = (x[i] - qx) ** 2 + (y[i] - qy) ** 2;
    if (d < bd) { bd = d; best = i; }
  }
  return best;
};

console.log("cellindex.js");

test("finds the exact point when the query is one of them", () => {
  const { x, y } = grid(20);
  const idx = buildIndex(x, y);
  for (const i of [0, 7, 199, 399]) {
    assert.equal(nearest(idx, x[i], y[i]), i);
  }
});

test("agrees with brute force on random queries", () => {
  const { x, y } = grid(40);                 // 1,600 points
  const idx = buildIndex(x, y);
  let seed = 7;
  const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  for (let t = 0; t < 200; t++) {
    const qx = rnd() * 65535, qy = rnd() * 65535;
    const a = nearest(idx, qx, qy);
    const b = brute(x, y, qx, qy);
    const da = (x[a] - qx) ** 2 + (y[a] - qy) ** 2;
    const db = (x[b] - qx) ** 2 + (y[b] - qy) ** 2;
    assert.equal(da, db, `query ${qx},${qy}: got ${a}, brute ${b}`);
  }
});

test("respects maxDist and returns -1 when nothing is close enough", () => {
  const x = Uint16Array.from([0]), y = Uint16Array.from([0]);
  const idx = buildIndex(x, y);
  assert.equal(nearest(idx, 60000, 60000, 100), -1);
  assert.equal(nearest(idx, 5, 5, 100), 0);
});

test("skips hidden points, which is what a region toggle needs", () => {
  const { x, y } = grid(10);
  const idx = buildIndex(x, y);
  const target = 44;
  const vis = (i) => i !== target;
  const got = nearest(idx, x[target], y[target], Infinity, vis);
  assert.notEqual(got, target, "a hidden point must not be returned");
  // compare DISTANCE, not index: on a regular grid several points are
  // equidistant from the hidden one, and the index and brute force are free to
  // break that tie differently
  const b = brute(x, y, x[target], y[target], vis);
  const d = (i) => (x[i] - x[target]) ** 2 + (y[i] - y[target]) ** 2;
  assert.equal(d(got), d(b));
});

test("a single point is found from anywhere", () => {
  const x = Uint16Array.from([30000]), y = Uint16Array.from([30000]);
  const idx = buildIndex(x, y);
  assert.equal(nearest(idx, 0, 0), 0);
  assert.equal(nearest(idx, 65535, 65535), 0);
});

test("an empty set returns -1 rather than throwing", () => {
  const idx = buildIndex(new Uint16Array(0), new Uint16Array(0));
  assert.equal(nearest(idx, 100, 100), -1);
});

test("every point lands in exactly one bucket", () => {
  const { x, y } = grid(30);
  const idx = buildIndex(x, y);
  assert.equal(idx.order.length, x.length);
  assert.equal(idx.starts[idx.cols * idx.cols], x.length);
  assert.equal(new Set(idx.order).size, x.length, "a point was placed twice");
});

test("duplicate positions do not lose points", () => {
  const x = Uint16Array.from([100, 100, 100]), y = Uint16Array.from([100, 100, 100]);
  const idx = buildIndex(x, y);
  assert.equal(new Set(idx.order).size, 3);
  assert.ok(nearest(idx, 100, 100) >= 0);
});

console.log(`\n${passed} cellindex checks passed`);
