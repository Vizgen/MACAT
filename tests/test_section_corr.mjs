// Geometry for the whole-section correlation scatter
// (web/frontend/src/sectionCorr.js).
//
// Run: node tests/test_section_corr.mjs
//
// Everything here is a way the plot can be WRONG WHILE STILL LOOKING LIKE A
// SCATTER, which is why it is worth pinning:
//   1. per-axis domains would tilt the identity line, so "above the line" would
//      stop meaning "detected more here than in the reference";
//   2. a log floor that differs from the one the backend correlates with means
//      the picture and the printed r are computed on different data;
//   3. a hit test in data space lands on the same corner every time, because
//      the floor packs the near-zero genes together.
import assert from "node:assert/strict";
import { EPS, toLog, domain, ticks, nearest, levelVerdict }
  from "../web/frontend/src/sectionCorr.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };
const pts = [
  { g: "Slc17a7", q: 12.0, r: 9.0 },
  { g: "Gad1",    q: 3.5,  r: 4.1 },
  { g: "Rare",    q: 0.0,  r: 0.002 },
  { g: "Absent",  q: 0.004, r: 0.0 },
];

test("the log floor matches the one the correlation uses", () => {
  assert.equal(EPS, 1e-4);
  assert.equal(toLog(0), Math.log10(EPS));
  assert.ok(Number.isFinite(toLog(0)), "a gene absent from one profile is finite");
  assert.equal(toLog(-5), Math.log10(EPS), "negatives clamp rather than NaN");
});

test("both axes share ONE domain, so the identity line stays at 45 degrees", () => {
  const d = domain(pts);
  // a domain computed per axis would differ; this one must cover both
  let lo = Infinity, hi = -Infinity;
  for (const p of pts) {
    for (const v of [toLog(p.q), toLog(p.r)]) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }
  assert.ok(d.lo <= lo && d.hi >= hi, "domain does not cover every point");
  // and it must be symmetric in q/r: swapping the axes cannot change it
  const swapped = domain(pts.map((p) => ({ ...p, q: p.r, r: p.q })));
  assert.ok(Math.abs(swapped.lo - d.lo) < 1e-12 && Math.abs(swapped.hi - d.hi) < 1e-12,
            "the domain depends on which axis a value is on");
});

test("a degenerate domain still has width", () => {
  const d = domain([{ g: "a", q: 1, r: 1 }, { g: "b", q: 1, r: 1 }]);
  assert.ok(d.hi > d.lo, "a single distinct value collapsed the domain");
  assert.equal(domain([]), null);
  assert.equal(domain(null), null);
});

test("ticks are whole decades inside the domain, and thinned", () => {
  const d = { lo: -4.3, hi: 1.4 };
  const t = ticks(d);
  assert.ok(t.every((e) => Number.isInteger(e)), "non-integer decade");
  assert.ok(t.every((e) => e >= d.lo && e <= d.hi), "tick outside the domain");
  assert.ok(ticks({ lo: -12, hi: 12 }, 6).length <= 6, "ticks were not thinned");
  assert.deepEqual(ticks(null), []);
});

test("the hit test works in SCREEN space, not data space", () => {
  // A square place(): x from r, y inverted from q, 100 px per decade.
  const place = (p) => [100 * toLog(p.r), -100 * toLog(p.q)];
  const target = pts[0];
  const [tx, ty] = place(target);
  assert.equal(nearest(pts, place, tx + 2, ty + 2).g, target.g);
  assert.equal(nearest(pts, place, tx, ty, 14).g, target.g);
  // far away -> nothing, rather than the least-bad point
  assert.equal(nearest(pts, place, tx + 500, ty + 500), null);
  // the two near-zero genes are ~0 apart in DATA space but far apart on screen;
  // a data-space test would confuse them
  const rare = pts[2], absent = pts[3];
  const [rx, ry] = place(rare);
  assert.equal(nearest(pts, place, rx, ry).g, rare.g);
  assert.notEqual(nearest(pts, place, rx, ry).g, absent.g);
});

test("the verdict names the right direction for each plane's axis", () => {
  // "posterior" about a sagittal section would simply be wrong
  assert.match(levelVerdict({ mm: 9.0, r: 0.9 }, 7.0, "AP", 0.5).text, /posterior/);
  assert.match(levelVerdict({ mm: 5.0, r: 0.9 }, 7.0, "AP", 0.5).text, /anterior/);
  assert.match(levelVerdict({ mm: 9.0, r: 0.9 }, 7.0, "ML", 0.5).text, /left/);
  assert.match(levelVerdict({ mm: 5.0, r: 0.9 }, 7.0, "ML", 0.5).text, /right/);
  assert.ok(!/posterior|anterior/.test(levelVerdict({ mm: 9.0, r: 0.9 }, 7.0, "ML", 0.5).text),
            "an ML verdict used AP direction words");
});

test("a peak within tolerance is agreement, whatever the r gain", () => {
  const v = levelVerdict({ mm: 7.32, r: 0.842 }, 7.65, "AP", 0.833);
  assert.equal(v.verdict, "agrees");
  assert.ok(Math.abs(v.offsetMm + 0.33) < 1e-9);
  // exactly at the tolerance still agrees
  assert.equal(levelVerdict({ mm: 7.5, r: 1 }, 7.0, "AP", 0.5, 0.5).verdict, "agrees");
});

test("a distant peak that is barely better is called INCONCLUSIVE", () => {
  // These curves are broad -- r stays within half its range over 7-9 mm -- so
  // the argmax wanders and distance alone would cry wolf on most jobs.
  const v = levelVerdict({ mm: 11.0, r: 0.841 }, 7.0, "AP", 0.833);
  assert.equal(v.verdict, "inconclusive");
  assert.match(v.text, /too flat/);
  assert.ok(!/worth a look/.test(v.text), "an inconclusive curve raised an alarm");
});

test("a distant peak that is materially better IS a mismatch", () => {
  const v = levelVerdict({ mm: 6.10, r: 0.802 }, 3.00, "ML", 0.748);
  assert.equal(v.verdict, "mismatch");
  assert.match(v.text, /worth a look/);
  assert.match(v.text, /3\.10 mm left/);
  assert.ok(Math.abs(v.gain - 0.054) < 1e-9);
});

test("with no r at the confirmed level it does not guess", () => {
  // rAtConfirmed omitted: cannot judge materiality, so it must not claim
  // "inconclusive" -- that would be an assertion it has no basis for.
  const v = levelVerdict({ mm: 11.0, r: 0.9 }, 7.0, "AP");
  assert.equal(v.verdict, "mismatch");
  assert.equal(v.gain, null);
  assert.ok(!/better there/.test(v.text), "it quoted a gain it does not have");
});

test("missing inputs give null rather than a sentence", () => {
  assert.equal(levelVerdict(null, 7.0, "AP", 0.8), null);
  assert.equal(levelVerdict({ mm: 7.0, r: 1 }, null, "AP", 0.8), null);
  assert.equal(levelVerdict({ r: 1 }, 7.0, "AP", 0.8), null);
});

console.log(`\n${passed} passed`);
