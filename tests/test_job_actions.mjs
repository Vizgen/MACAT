// The Resume list's download and delete rules (web/frontend/src/jobActions.js).
//
// Run: node tests/test_job_actions.mjs
//
// Download: only COMPLETED jobs, with a notice -- not a confirmation -- saying
// how many of the selection will come and which are skipped. Delete: a
// confirmation that names the jobs, says it is permanent, and calls out
// finished results. The wording is the feature, so it is pinned here.
import assert from "node:assert/strict";
import { downloadPlan, deleteWarning, busyAmong, isBusy, formatCreated, createdText,
         JOB_COLUMNS, DEFAULT_JOB_SORT, knownLevelText,
         curationConfirmable } from "../web/frontend/src/jobActions.js";
import { sortRows, nextSort } from "../web/frontend/src/sortable.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };

const J = (id, state, extra = {}) => ({ id, name: id, state, ...extra });

test("all completed: one URL, no notice", () => {
  const p = downloadPlan([J("a", "COMPLETED"), J("b", "COMPLETED")]);
  assert.equal(p.url, "/api/reports.zip?ids=a,b");
  assert.equal(p.notice, null);
});

test("a single completed job uses its own report.zip", () => {
  assert.equal(downloadPlan([J("a", "COMPLETED")]).url, "/api/jobs/a/report.zip");
});

test("a mix says how many of how many, and names the skipped", () => {
  const p = downloadPlan([J("a", "COMPLETED"), J("b", "INPUT_READY"),
                          J("c", "FAILED"), J("d", "COMPLETED")]);
  assert.equal(p.url, "/api/reports.zip?ids=a,d");
  assert.equal(p.notice, "2 of 4 selected will be downloaded. "
    + "Not completed, so skipped: b (INPUT_READY), c (FAILED).");
});

test("nothing completed: no URL, and the notice says so", () => {
  const p = downloadPlan([J("b", "INPUT_READY")]);
  assert.equal(p.url, null);
  assert.match(p.notice, /^None of the 1 selected jobs is completed/);
  assert.match(p.notice, /b \(INPUT_READY\)/);
});

test("ids are URL-encoded", () => {
  assert.equal(downloadPlan([J("a b", "COMPLETED"), J("c&d", "COMPLETED")]).url,
               "/api/reports.zip?ids=a%20b,c%26d");
});

test("the delete warning is explicit and permanent", () => {
  const w = deleteWarning([J("a", "COMPLETED"), J("b", "FAILED")]);
  assert.match(w, /^Permanently delete these 2 jobs\?/);
  assert.match(w, /a \(COMPLETED\), b \(FAILED\)/);
  assert.match(w, /cannot be undone/);
  assert.match(w, /1 of them are COMPLETED: download the report \.zip first/);
});

test("one job reads as one job", () => {
  const w = deleteWarning([J("a", "COMPLETED")]);
  assert.match(w, /^Permanently delete this job\?/);
  assert.match(w, /It is COMPLETED/);
});

test("nothing finished, no results line", () => {
  assert.doesNotMatch(deleteWarning([J("x", "INPUT_READY")]), /COMPLETED:/);
});

test("a long selection is listed, then counted", () => {
  const many = Array.from({ length: 15 }, (_, i) => J(`j${i}`, "FAILED"));
  assert.match(deleteWarning(many), /, and 3 more/);
});

test("running jobs are refused up front", () => {
  assert.equal(isBusy(J("f", "FINALIZING")), true);
  assert.equal(isBusy(J("r", "AP_CONFIRMED", { register: { status: "running" } })), true);
  assert.equal(isBusy(J("c", "COMPLETED")), false);
  assert.deepEqual(busyAmong([J("f", "FINALIZING"), J("c", "COMPLETED")]).map((j) => j.id), ["f"]);
});

test("created time is UTC and agrees with the job ID it sits beside", () => {
  // a real job: 20261007_162344_6bc0c8 was created at 1791390224.81266
  assert.equal(formatCreated(1791390224.81266), "2026-10-07 16:23");
  const id = "20261007_162344_6bc0c8";
  const shown = formatCreated(1791390224.81266).replace(/[-: ]/g, "");
  assert.equal(id.replace("_", "").slice(0, 12), shown, "time and folder name disagree");
  assert.equal(formatCreated(undefined), "");
  assert.equal(formatCreated("nonsense"), "");
});

test("the server's own formatting wins, so the column follows the server's zone", () => {
  assert.equal(createdText({ created: 1791390224.8, created_text: "2026-10-07 12:23" }),
               "2026-10-07 12:23");
  // an older server without created_text falls back to UTC
  assert.equal(createdText({ created: 1791390224.8 }), "2026-10-07 16:23");
});

test("the list opens newest first, as it always did", () => {
  const js = [J("old", "COMPLETED", { created: 100 }), J("new", "FAILED", { created: 300 }),
              J("mid", "INPUT_READY", { created: 200 })];
  assert.deepEqual(sortRows(js, JOB_COLUMNS, DEFAULT_JOB_SORT).map((j) => j.id),
                   ["new", "mid", "old"]);
});

test("every data column sorts, and a second click reverses", () => {
  const js = [J("b_2", "FAILED", { created: 2, notes: "b" }),
              J("a_1", "COMPLETED", { created: 1, notes: "a" })];
  for (const col of JOB_COLUMNS) {
    const s1 = nextSort(col, DEFAULT_JOB_SORT);
    const s2 = nextSort(col, s1);
    assert.notDeepEqual(sortRows(js, JOB_COLUMNS, s1).map((j) => j.id),
                        sortRows(js, JOB_COLUMNS, s2).map((j) => j.id), col.key);
  }
  assert.deepEqual(JOB_COLUMNS.map((c) => c.label), ["Job", "State", "Created time", "Job ID", "Notes"]);
});

test("jobs without notes sort after those with, either way", () => {
  const js = [J("none", "FAILED"), J("b", "FAILED", { notes: "beta" }),
              J("blank", "FAILED", { notes: "  " }), J("a", "FAILED", { notes: "alpha" })];
  const col = JOB_COLUMNS.find((c) => c.key === "notes");
  const up = nextSort(col, DEFAULT_JOB_SORT);
  assert.deepEqual(sortRows(js, JOB_COLUMNS, up).map((j) => j.id).slice(0, 2), ["a", "b"]);
  assert.deepEqual(sortRows(js, JOB_COLUMNS, nextSort(col, up)).map((j) => j.id).slice(0, 2),
                   ["b", "a"]);
});

test("a known level reads as the step-3 index, with the sagittal mirror", () => {
  assert.equal(knownLevelText({ idx: 176, tol: 7, mirror: null }), "level 176 ±7");
  assert.equal(knownLevelText({ idx: 130, tol: 3, mirror: 325 }), "level 130 ±3, or 325");
  assert.equal(knownLevelText(null), "");
});

// Step 5's Confirm alignment (found in user testing, 2026-11-03): an
// unchanged automatic fit is a valid curation, so it must be confirmable.
test("an unchanged automatic fit can be confirmed", () => {
  assert.equal(curationConfirmable("AUTO_REGISTRATION_DONE", false), true);
  assert.equal(curationConfirmable("FAILED", false), true);
});

test("an edit can always be confirmed", () => {
  for (const s of ["AUTO_REGISTRATION_DONE", "MANUAL_CURATION_CONFIRMED", "COMPLETED"])
    assert.equal(curationConfirmable(s, true), true, s);
});

test("an already-confirmed job needs an edit to confirm again", () => {
  for (const s of ["MANUAL_CURATION_CONFIRMED", "FINALIZING", "COMPLETED"])
    assert.equal(curationConfirmable(s, false), false, s);
});

console.log(`${passed} passed`);
