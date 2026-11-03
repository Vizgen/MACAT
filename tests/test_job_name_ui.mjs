// The job-name field's input rule (web/frontend/src/jobapi.js).
//
// Run: node tests/test_job_name_ui.mjs
//
// The name reaches a Content-Disposition header and zip entry names. The server
// sanitises again -- that is the boundary -- but the field enforces the rule
// while the user types so they see the name they will actually get, rather than
// discovering it in a downloaded filename.
//
// The prefill goes through the SAME rule. A prefilled value that violated the
// field's own restriction would be the worst of both: it looks accepted, and
// the user has no idea why their job came out named something else.
import assert from "node:assert/strict";
import { cleanName, autoJobName, NAME_MAX, isS3Path, s3AutoKey }
  from "../web/frontend/src/jobapi.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };

test("the allowed set survives untouched", () => {
  for (const ok of ["region_R1", "tr_202401020900_R1", "Sample A11 rep2",
                    "a-b_c 1", "ABCxyz0189", "-_ -_"]) {
    assert.equal(cleanName(ok), ok, `${ok} was altered`);
  }
});

test("everything that could steer a filename is dropped", () => {
  for (const [input, want] of [
    ["../../etc/passwd", "etcpasswd"],
    ["a/b", "ab"],
    ["a\\b", "ab"],
    ['a"b', "ab"],
    ["a';b", "ab"],
    ["a\nb", "ab"],
    ["a\tb", "ab"],
    ["A11 (rep2)", "A11 rep2"],
    ["100%", "100"],
    ["a.b.csv", "abcsv"],
  ]) {
    assert.equal(cleanName(input), want);
  }
  // and nothing dangerous can survive ANY input
  const hostile = cleanName('../".;\n\t\\%$&|<>*?:' + "'");
  assert.match(hostile, /^[A-Za-z0-9 _-]*$/, `${hostile} escaped the rule`);
});

test("the length cap matches the server's", () => {
  assert.equal(cleanName("x".repeat(500)).length, NAME_MAX);
  assert.equal(NAME_MAX, 120, "the cap drifted from web/jobs.MAX_JOB_NAME");
});

test("empty and nullish inputs give an empty string, never a crash", () => {
  for (const v of ["", null, undefined, "   ".replace(/ /g, "\t")]) {
    assert.equal(typeof cleanName(v), "string");
  }
  assert.equal(cleanName(null), "");
});

test("the prefill is the folder name, cleaned by the SAME rule", () => {
  assert.equal(autoJobName("/data/x/202608261855_MsBrain-Example/region_R1", ""),
               "region_R1");
  // a trailing slash must not swallow the segment
  assert.equal(autoJobName("/data/x/region_R1/", ""), "region_R1");
  assert.equal(autoJobName("/data/x/region_R1///", ""), "region_R1");
  // a folder name carrying forbidden characters is cleaned, not shown raw
  assert.equal(autoJobName("/data/x/run (2).final", ""), "run 2final");
  assert.match(autoJobName("/data/x/a.b%c", ""), /^[A-Za-z0-9 _-]*$/);
});

test("the region key is used when there is no path", () => {
  assert.equal(autoJobName("", "tr_202401020900_R1"), "tr_202401020900_R1");
  assert.equal(autoJobName("   ", "tr_x_R1"), "tr_x_R1");
  assert.equal(autoJobName("", ""), "");
});

test("a path beats a region key, matching what the job would be created from", () => {
  assert.equal(autoJobName("/data/x/region_R9", "tr_other_R1"), "region_R9");
});

test("an s3:// path is named like the server names it", () => {
  // web/s3source.auto_key gives the same (tests/test_s3source.py)
  const url = "s3://bkt/202401020900_example-experiment/region_R1/";
  assert.equal(isS3Path(url), true);
  assert.equal(isS3Path("/data/x/region_R1"), false);
  assert.equal(s3AutoKey(url), "s3_202401020900_R1");
  assert.equal(autoJobName(url, ""), "s3_202401020900_R1",
               "not region_R1, which every experiment's region folder is called");
});

console.log(`\n${passed} passed`);
