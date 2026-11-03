// Step 1's "Your sections" display helpers (web/frontend/src/dataFolder.js).
//
// Run: node tests/test_data_folder.mjs
import assert from "node:assert/strict";
import { groupByExperiment, sectionForPath, scanStatus } from "../web/frontend/src/dataFolder.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };

const S = (experiment, region, path) => ({ experiment, region, path, name: `${experiment}_${region}` });
const secs = [S("exp_a", "region_R1", "/d/exp_a/region_R1"), S("exp_a", "region_R2", "/d/exp_a/region_R2"),
              S("exp_b", "region_S1", "s3://b/p/exp_b/region_S1/")];

test("sections group by experiment, in order", () => {
  const g = groupByExperiment(secs);
  assert.deepEqual(g.map(([e, s]) => [e, s.map((x) => x.region)]),
                   [["exp_a", ["region_R1", "region_R2"]], ["exp_b", ["region_S1"]]]);
  assert.deepEqual(groupByExperiment(null), []);
});

test("a typed path finds its section, with or without the trailing slash", () => {
  assert.equal(sectionForPath(secs, "/d/exp_a/region_R2/").region, "region_R2");
  assert.equal(sectionForPath(secs, "s3://b/p/exp_b/region_S1").region, "region_S1");
  assert.equal(sectionForPath(secs, "/elsewhere"), null);
  assert.equal(sectionForPath(secs, ""), null);
});

test("the status line says what was found, or what went wrong", () => {
  const t = () => "T";
  assert.equal(scanStatus({ folder: null }), "");
  assert.equal(scanStatus({ folder: "/d", sections: secs, scanned_at: 1 }, t), "3 sections · scanned T");
  assert.equal(scanStatus({ folder: "/d", sections: [secs[0]], scanned_at: 1 }, t), "1 section · scanned T");
  assert.match(scanStatus({ folder: "/d", sections: [], scanned_at: 1 }, t), /No sections found/);
  assert.equal(scanStatus({ folder: "/d", error: "not a folder" }, t), "Could not scan: not a folder");
  assert.equal(scanStatus({ folder: "/d", sections: secs, scanned_at: 1, truncated: true }, t),
               "3+ sections · scanned T");
});

console.log(`${passed} passed`);
