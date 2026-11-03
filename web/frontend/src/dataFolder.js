// Step 1's "Your sections": the sections found in the user's data folder.
//
// The server scans <data folder>/<experiment>/region_* (web/datafolder.py);
// these helpers only shape what it returns for display. Framework-free so
// tests/test_data_folder.mjs can pin them.

/** Sections grouped by experiment, in the server's (sorted) order:
 *  [[experiment, [section, ...]], ...]. */
export function groupByExperiment(sections) {
  const by = new Map();
  for (const s of sections || []) {
    if (!by.has(s.experiment)) by.set(s.experiment, []);
    by.get(s.experiment).push(s);
  }
  return [...by.entries()];
}

/** The section whose folder is `path`, ignoring a trailing slash; else null. */
export function sectionForPath(sections, path) {
  const norm = (p) => String(p || "").trim().replace(/\/+$/, "");
  const want = norm(path);
  if (!want) return null;
  return (sections || []).find((s) => norm(s.path) === want) || null;
}

/** "12 sections · scanned 2026-10-28 14:02", or what went wrong. */
export function scanStatus(df, fmtTime = (t) => new Date(t * 1000).toLocaleString()) {
  if (!df || !df.folder) return "";
  if (df.error) return `Could not scan: ${df.error}`;
  if (df.scanned_at == null) return "Not scanned yet.";
  const n = (df.sections || []).length;
  const count = n === 0
    ? "No sections found (expected <experiment>/region_*/ folders)"
    : `${n}${df.truncated ? "+" : ""} section${n === 1 ? "" : "s"}`;
  return `${count} · scanned ${fmtTime(df.scanned_at)}`;
}
