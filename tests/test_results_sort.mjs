// Behaviour tests for the Results table sorting (web/frontend/src/sortable.js).
//
// Run: node tests/test_results_sort.mjs
//
// The ordering lives in a plain .js module rather than inside ResultsStep.jsx so
// it can be driven directly here; node cannot parse JSX, so the <SortTh> half is
// presentation only and is not imported.
import assert from "node:assert/strict";
import { nextSort, sortRows } from "../web/frontend/src/sortable.js";

let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log("  ok  " + name); };

// The real Region composition columns: rows are [acronym, name, cells, div, color].
const COLS = [
  { key: "acronym", label: "Allen acronym", value: (r) => r[0] },
  { key: "name", label: "Region name", value: (r) => r[1] },
  { key: "cells", label: "Cells", value: (r) => r[2] || 0, numeric: true },
];
const ROWS = [
  ["MB", "Midbrain", 5980],
  ["CA1", "Field CA1", 4628],
  ["CA3", "Field CA3", 4137],
  ["ZI", "Zona incerta", 2507],
  ["alv", "alveus", 1910],
];
const acro = (rows) => rows.map((r) => r[0]);

console.log("sortable.js");

test("a numeric column opens descending, text opens ascending", () => {
  assert.deepEqual(nextSort(COLS[2], null), { key: "cells", dir: "desc" });
  assert.deepEqual(nextSort(COLS[0], null), { key: "acronym", dir: "asc" });
});

test("clicking the active column reverses it", () => {
  const first = nextSort(COLS[2], null);
  assert.deepEqual(nextSort(COLS[2], first), { key: "cells", dir: "asc" });
  assert.deepEqual(nextSort(COLS[2], { key: "cells", dir: "asc" }),
                   { key: "cells", dir: "desc" });
});

test("clicking a different column does not inherit the old direction", () => {
  const sort = nextSort(COLS[0], { key: "cells", dir: "asc" });
  assert.deepEqual(sort, { key: "acronym", dir: "asc" });
});

test("numeric sorting is by value, not by string", () => {
  // "5980" < "999" as strings; the numeric column must not do that
  const rows = [["a", "", 999], ["b", "", 5980]];
  assert.deepEqual(acro(sortRows(rows, COLS, { key: "cells", dir: "desc" })),
                   ["b", "a"]);
});

test("descending then ascending are exact reverses", () => {
  const down = acro(sortRows(ROWS, COLS, { key: "cells", dir: "desc" }));
  const up = acro(sortRows(ROWS, COLS, { key: "cells", dir: "asc" }));
  assert.deepEqual(down, ["MB", "CA1", "CA3", "ZI", "alv"]);
  assert.deepEqual(up, [...down].reverse());
});

test("text sorting is case-insensitive enough to put alv with the letters", () => {
  // localeCompare, not codepoint order: "alv" must not be exiled after "ZI"
  const up = acro(sortRows(ROWS, COLS, { key: "acronym", dir: "asc" }));
  assert.deepEqual(up, ["alv", "CA1", "CA3", "MB", "ZI"]);
});

test("an unknown or absent sort returns the rows untouched", () => {
  assert.equal(sortRows(ROWS, COLS, null), ROWS);
  assert.equal(sortRows(ROWS, COLS, { key: "nope", dir: "asc" }), ROWS);
});

test("the input array is never mutated", () => {
  const before = acro(ROWS);
  sortRows(ROWS, COLS, { key: "cells", dir: "asc" });
  assert.deepEqual(acro(ROWS), before);
});

test("ties keep the order they arrived in, in both directions", () => {
  const tied = [["x", "", 10], ["y", "", 10], ["z", "", 10]];
  assert.deepEqual(acro(sortRows(tied, COLS, { key: "cells", dir: "desc" })),
                   ["x", "y", "z"]);
  assert.deepEqual(acro(sortRows(tied, COLS, { key: "cells", dir: "asc" })),
                   ["x", "y", "z"]);
});

test("missing values sort last in BOTH directions", () => {
  // flipping the arrow must not parade the blanks to the top
  const cols = [{ key: "m", label: "m", value: (r) => r[1] }];
  const rows = [["a", "b"], ["b", null], ["c", "a"]];
  assert.deepEqual(sortRows(rows, cols, { key: "m", dir: "asc" }).map((r) => r[0]),
                   ["c", "a", "b"]);
  assert.deepEqual(sortRows(rows, cols, { key: "m", dir: "desc" }).map((r) => r[0]),
                   ["a", "c", "b"]);
});

test("the collapsed top-8 is the head of the CURRENT order", () => {
  // ResultsStep slices AFTER sorting, so the collapsed table follows the reader
  const byName = sortRows(ROWS, COLS, { key: "name", dir: "asc" }).slice(0, 2);
  assert.deepEqual(acro(byName), ["alv", "CA1"]);
});

console.log(`\n${passed} sortable checks passed`);

// --------------------------------------------------------------- pagination
import { pageSlice } from "../web/frontend/src/sortable.js";

const N = (n) => Array.from({ length: n }, (_, i) => i);

console.log("\npageSlice");

test("a full page in the middle", () => {
  const p = pageSlice(N(136), 2, 10);
  assert.deepEqual(p.rows, [20,21,22,23,24,25,26,27,28,29]);
  assert.deepEqual([p.page, p.pages, p.from, p.to, p.total], [2, 14, 21, 30, 136]);
});

test("the last page is short, not padded", () => {
  const p = pageSlice(N(136), 13, 10);
  assert.deepEqual(p.rows, [130,131,132,133,134,135]);
  assert.deepEqual([p.from, p.to], [131, 136]);
});

test("a page past the end snaps to the last real page", () => {
  // sorting or a smaller job can strand the caller on a page that is now gone;
  // an empty table would look like a bug in the data
  const p = pageSlice(N(136), 99, 10);
  assert.equal(p.page, 13);
  assert.equal(p.rows.length, 6);
});

test("a negative page snaps to the first", () => {
  assert.equal(pageSlice(N(50), -3, 10).page, 0);
});

test("no rows at all is a single empty page, not zero pages", () => {
  const p = pageSlice([], 0, 10);
  assert.deepEqual([p.rows.length, p.pages, p.from, p.to, p.total], [0, 1, 0, 0, 0]);
});

test("fewer rows than one page", () => {
  const p = pageSlice(N(6), 0, 10);
  assert.deepEqual([p.pages, p.from, p.to], [1, 1, 6]);
});

console.log(`\n${passed} sortable checks passed (including pagination)`);
