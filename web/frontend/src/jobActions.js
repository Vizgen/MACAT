// What the "Resume a job" list does with a selection: download reports, or
// delete jobs. Kept apart from the component so the rules -- and the exact
// warnings, which are the point -- are testable in plain Node
// (tests/test_job_actions.mjs).

export const COMPLETED = "COMPLETED";

const label = (j) => j.name || j.region_key || j.id;

/** States in which this job's curation has already been confirmed. */
const CURATION_CONFIRMED = ["MANUAL_CURATION_CONFIRMED", "FINALIZING", COMPLETED];

/** Whether step 5's Confirm alignment may be pressed, as far as edits go.
 *  Accepting the automatic fit unchanged is a valid curation, so a job that has
 *  not been confirmed yet may confirm with no edit. Once confirmed, an unchanged
 *  fit would only re-run finalize for the same result, so it needs an edit. */
export function curationConfirmable(state, dirty) {
  return !!dirty || !CURATION_CONFIRMED.includes(state);
}

/** A worker is writing to this job: deleting it now would pull files out from
 *  under a registration or a finalize. Mirrors web/jobs.is_busy. */
export function isBusy(j) {
  return j.state === "FINALIZING" || j.register?.status === "running";
}

function listOf(jobs, max = 8) {
  const shown = jobs.slice(0, max).map((j) => `${label(j)} (${j.state})`);
  const more = jobs.length - shown.length;
  return shown.join(", ") + (more > 0 ? `, and ${more} more` : "");
}

/** Download only finished jobs, and say so -- no confirmation, by design: the
 *  notice is information, not a question. Returns the URL to fetch (null when
 *  nothing can be downloaded) and the notice to show (null when there is
 *  nothing worth saying, i.e. every selected job is completed). */
export function downloadPlan(selected) {
  const ready = selected.filter((j) => j.state === COMPLETED);
  const notReady = selected.filter((j) => j.state !== COMPLETED);
  let url = null;
  if (ready.length === 1) url = `/api/jobs/${ready[0].id}/report.zip`;
  else if (ready.length > 1) {
    url = `/api/reports.zip?ids=${ready.map((j) => encodeURIComponent(j.id)).join(",")}`;
  }
  let notice = null;
  if (selected.length && !ready.length) {
    notice = `None of the ${selected.length} selected jobs is completed, so there is `
      + `nothing to download. Not completed: ${listOf(notReady)}.`;
  } else if (notReady.length) {
    notice = `${ready.length} of ${selected.length} selected will be downloaded. `
      + `Not completed, so skipped: ${listOf(notReady)}.`;
  }
  return { ready, notReady, url, notice };
}

/** The text of the delete confirmation. It names every job (up to a point),
 *  says the deletion is permanent, and calls out finished results, because a
 *  COMPLETED job is the one whose loss costs real work. */
export function deleteWarning(selected) {
  const n = selected.length;
  const done = selected.filter((j) => j.state === COMPLETED).length;
  return [
    `Permanently delete ${n === 1 ? "this job" : `these ${n} jobs`}?`,
    "",
    listOf(selected, 12),
    "",
    "This removes each job and its registration, curation and results from the "
      + "server. It cannot be undone. Files that another job still uses (older "
      + "jobs on the same section shared some) are kept.",
    ...(done
      ? ["", `${done === n ? (n === 1 ? "It is" : "All are") : `${done} of them are`} `
          + "COMPLETED: download the report .zip first if you need the results."]
      : []),
  ].join("\n");
}

/** Jobs a delete must refuse up front (the server refuses them too). */
export function busyAmong(selected) {
  return selected.filter(isBusy);
}

/** The Created time to show: the server's own formatting (`created_text`), so
 * the column follows the SERVER's time zone -- the clock that stamps the job ID,
 * and so the folder name. Never the browser's: a viewer four hours behind would
 * otherwise read "12:23" beside folder "20261007_162344". */
export function createdText(j) {
  return j.created_text ?? formatCreated(j.created);
}

/** Fallback for a response without `created_text`: Unix seconds as
 * "2026-10-07 16:23" in UTC, which is the server's zone. */
export function formatCreated(ts) {
  if (ts == null || !Number.isFinite(Number(ts))) return "";
  const d = new Date(Number(ts) * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} `
    + `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/** The Resume list's sortable columns (sortable.js specs). The checkbox and the
 *  action buttons are controls, not data, and do not sort. */
export const JOB_COLUMNS = [
  { key: "name", label: "Job", value: (j) => label(j) },
  { key: "state", label: "State", value: (j) => j.state },
  { key: "created", label: "Created time", value: (j) => j.created ?? null, numeric: true },
  { key: "id", label: "Job ID", value: (j) => j.id },
  // blank notes sort last in both directions (sortable.js puts null last)
  { key: "notes", label: "Notes", value: (j) => (j.notes || "").trim() || null },
];

/** The level a known-answer section should be set to in step 3, as text:
 *  "level 176 ±7", plus the mirror index for sagittal ("level 130 ±3, or 325").
 *  Empty for anything without an answer key. */
export function knownLevelText(k) {
  if (!k || k.idx == null) return "";
  return `level ${k.idx} ±${k.tol}` + (k.mirror != null ? `, or ${k.mirror}` : "");
}

/** Newest first: the order the list always had before it could be sorted. */
export const DEFAULT_JOB_SORT = { key: "created", dir: "desc" };
