// How steps 4-6 word cells that lie past the end of the Allen atlas
// (overhangSummary in web/frontend/src/jobapi.js).
//
// Run: node tests/test_overhang_ui.mjs
//
// One formatter for three steps, so the same job never reads "5.1%" in one
// place and "5%" in another. It must also say NOTHING when there is nothing to
// say: that is the normal coronal case, and a "0 cells beyond the atlas" line on
// every job would teach people to ignore the row on the jobs where it matters.
import assert from "node:assert/strict";
import { overhangSummary } from "../web/frontend/src/jobapi.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };

test("nothing to report is null, not a zero line", () => {
  assert.equal(overhangSummary(null), null);
  assert.equal(overhangSummary(undefined), null);
  assert.equal(overhangSummary({ n: 0, frac: 0, ends: [] }), null);
});

test("a sagittal overhang names both AP ends", () => {
  const s = overhangSummary({
    n: 7300, frac: 0.0876, n_cells: 83300,
    ends: [{ axis: "AP", end: "anterior", n: 4200, frac: 0.0504 },
           { axis: "AP", end: "posterior", n: 3100, frac: 0.0372 }],
  });
  assert.equal(s.n, 7300);
  assert.equal(s.pct, "8.8%");
  assert.equal(s.ends, "anterior 5.0%, posterior 3.7%");
});

test("a sliver is not rounded away to 0.0%", () => {
  const s = overhangSummary({ n: 3, frac: 0.00004,
    ends: [{ axis: "AP", end: "posterior", n: 3, frac: 0.00004 }] });
  assert.equal(s.pct, "<0.1%");
  assert.equal(s.ends, "posterior <0.1%");
});

test("a result from before the field existed is simply silent", () => {
  assert.equal(overhangSummary({}.atlas_overhang), null);
});

console.log(`${passed} passed`);
