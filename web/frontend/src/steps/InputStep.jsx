import React, { useEffect, useState } from "react";
import { API, jget, jpost, pickMaxPx, cleanName, autoJobName, NAME_MAX, isS3Path }
  from "../jobapi";
import { COMPLETED, downloadPlan, deleteWarning, busyAmong, isBusy,
         createdText, JOB_COLUMNS, DEFAULT_JOB_SORT, knownLevelText } from "../jobActions";
import { sortRows } from "../sortable";
import SortTh from "../SortTh";
import { groupByExperiment, sectionForPath, scanStatus } from "../dataFolder";

/** The data folder: where "Your sections" comes from (web/datafolder.py).
 *  Scanned only when set or on Rescan, never on page load. Picking a section
 *  fills the experiment-folder box, so everything after that is unchanged. */
function DataFolderPanel({ df, setDf, path, onPick }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  if (!df) return null;
  if (!df.available) {
    return <div className="muted" style={{ fontSize: 11, marginTop: 10 }}>{df.reason}</div>;
  }
  const run = async (fn) => {
    setBusy(true); setErr(null);
    try { setDf(await fn()); setEditing(false); } catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  };
  const save = () => run(() => jpost("/api/data_folder", { folder: draft }));
  const picked = sectionForPath(df.sections, path);
  return (
    <>
      <h2>Data folder</h2>
      {editing ? (
        <>
          <input type="text" value={draft} autoFocus disabled={busy}
                 placeholder="/path/to/experiments  or  s3://bucket/prefix/"
                 onChange={(e) => setDraft(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
                 style={{ width: "100%" }} />
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            The folder that holds your experiments as MERSCOPE writes them:{" "}
            <code>&lt;experiment&gt;/region_*/</code>, each with its cell tables and{" "}
            <code>images/</code>. Leave blank to clear.
          </div>
          <div className="row" style={{ gap: 6, marginTop: 6 }}>
            <button className="primary" onClick={save} disabled={busy}>
              {busy ? "Scanning…" : "Save"}</button>
            <button onClick={() => setEditing(false)} disabled={busy}>Cancel</button>
          </div>
        </>
      ) : (
        <>
          <div style={{ fontSize: 12, wordBreak: "break-all" }}>
            {df.folder ? <code>{df.folder}</code> : <span className="muted">None set.</span>}
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            {busy ? "Scanning…" : scanStatus(df)}
            {df.locked && <> Set by <code>{df.env}</code> when MACAT starts.</>}
          </div>
          {!df.locked && (
            <div className="row" style={{ gap: 6, marginTop: 6 }}>
              <button onClick={() => { setDraft(df.folder || ""); setEditing(true); setErr(null); }}
                      disabled={busy}>{df.folder ? "Change" : "Set…"}</button>
              {df.folder && (
                <button onClick={() => run(() => jpost("/api/data_folder/rescan", {}))}
                        disabled={busy}>Rescan</button>
              )}
            </div>
          )}
          {df.locked && df.folder && (
            <button style={{ marginTop: 6 }} disabled={busy}
                    onClick={() => run(() => jpost("/api/data_folder/rescan", {}))}>Rescan</button>
          )}
        </>
      )}
      {err && <div style={{ color: "#f85149", fontSize: 12, marginTop: 6 }}>{err}</div>}

      {!editing && (df.sections || []).length > 0 && (
        <>
          <h2>Your sections</h2>
          <select value={picked ? picked.path : ""} style={{ width: "100%" }}
                  onChange={(e) => onPick(e.target.value)}>
            <option value="">— select —</option>
            {groupByExperiment(df.sections).map(([exp, secs]) => (
              <optgroup key={exp} label={exp}>
                {secs.map((s) => (
                  <option key={s.path} value={s.path}>
                    {s.region}
                    {s.known_level ? `  ·  ${knownLevelText(s.known_level)}` : ""}
                    {s.dapi ? "  ·  DAPI" : ""}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </>
      )}
    </>
  );
}

function warmOrientationImage(job) {
  return new Promise((resolve) => {
    const img = new Image();
    const done = () => resolve();
    img.onload = done;
    img.onerror = done;
    img.src = `${API}/api/jobs/${job.id}/base.png?max_px=${pickMaxPx()}`;
    if (img.complete) done();
  });
}

/** The Notes cell: edited in place, saved when it loses focus or on Enter;
 *  Escape puts back what was saved. Never opens the job. */
function NotesCell({ job, onSaved }) {
  const [text, setText] = useState(job.notes || "");
  const [state, setState] = useState(null);           // null | "saving" | "error"
  useEffect(() => { setText(job.notes || ""); }, [job.notes]);
  const save = async () => {
    if (text.trim() === (job.notes || "").trim()) return;
    setState("saving");
    try {
      await jpost(`/api/jobs/${job.id}/notes`, { notes: text });
      setState(null);
      onSaved();
    } catch {
      setState("error");
    }
  };
  return (
    <td className="notes" onClick={(e) => e.stopPropagation()}>
      <input type="text" value={text} maxLength={500} placeholder="add a note"
             aria-label={`Notes for ${job.name || job.id}`}
             className={state === "error" ? "error" : ""}
             title={state === "error" ? "Not saved: the server refused it. Try again." : text}
             onChange={(e) => setText(e.target.value)}
             onBlur={save}
             onKeyDown={(e) => {
               if (e.key === "Enter") e.currentTarget.blur();
               if (e.key === "Escape") { setText(job.notes || ""); setState(null); }
             }} />
    </td>
  );
}

export default function InputStep({ onCreate }) {
  const [jobs, setJobs] = useState([]);
  const [path, setPath] = useState("");
  // The job label. Prefilled from whichever source is chosen and left editable;
  // `touched` is what stops the prefill from overwriting something the user has
  // typed when they then change the selection.
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [openingId, setOpeningId] = useState(null);
  const [err, setErr] = useState(null);
  // s3:// paths only: also pull the DAPI mosaic (large) in the background
  const [fetchDapi, setFetchDapi] = useState(false);
  // Resume-list selection, and the line under its toolbar: the download notice
  // or the outcome of a delete. One line, replaced by the next action.
  const [checked, setChecked] = useState(() => new Set());
  const [notice, setNotice] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [jobSort, setJobSort] = useState(DEFAULT_JOB_SORT);
  // Where job folders live on the server, for the note under the table. Asked
  // for rather than hardcoded: ALLEN_OUT_ROOT can move the whole store.
  const [jobsDir, setJobsDir] = useState(null);
  const [serverTz, setServerTz] = useState(null);

  // the data folder and its last scan (null until loaded or if unavailable)
  const [df, setDf] = useState(null);

  const reloadJobs = () => jget("/api/jobs").then((js) => {
    setJobs(js);
    // drop selections of jobs that no longer exist
    setChecked((c) => new Set([...c].filter((id) => js.some((j) => j.id === id))));
  }).catch(() => {});

  useEffect(() => {
    jget("/api/data_folder").then(setDf).catch(() => setDf(null));
    reloadJobs();
    jget("/api/jobs_dir").then((r) => {
      setJobsDir(r.jobs_dir); setServerTz(r.server_tz || null);
    }).catch(() => {});
  }, []);

  const selected = jobs.filter((j) => checked.has(j.id));
  const allChecked = jobs.length > 0 && checked.size === jobs.length;
  const toggle = (id) => setChecked((c) => {
    const n = new Set(c);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  // No confirmation: the notice says what is coming and what is skipped, and
  // the browser's own download is the rest.
  const download = (list) => {
    const plan = downloadPlan(list);
    setNotice(plan.notice ? { kind: "warn", text: plan.notice } : null);
    if (plan.url) window.location.href = `${API}${plan.url}`;
  };

  const remove = async (list) => {
    const busy = busyAmong(list);
    if (busy.length) {
      setNotice({ kind: "error", text: "Cannot delete while still running: "
        + busy.map((j) => j.name || j.id).join(", ") + ". Wait for it to finish." });
      return;
    }
    if (!window.confirm(deleteWarning(list))) return;
    setDeleting(true);
    try {
      const r = await jpost("/api/jobs/delete", { ids: list.map((j) => j.id) });
      setNotice({ kind: "ok", text: `Deleted ${r.deleted.length} job`
        + `${r.deleted.length === 1 ? "" : "s"}`
        + (r.files_kept_shared ? `; kept ${r.files_kept_shared} file`
          + `${r.files_kept_shared === 1 ? "" : "s"} another job still uses.` : ".") });
    } catch (e) {
      setNotice({ kind: "error", text: String(e.message || e) });
    } finally {
      setDeleting(false);
      reloadJobs();
    }
  };

  // What the backend would call this job if no label were given: the region key,
  // or the last path segment. Shown as the prefill so the field starts at
  // today's behaviour rather than empty.
  // A section picked from the data folder is named <experiment>_<region>: the
  // bare folder name would be "region_R1" for every region of every experiment.
  const autoName = React.useMemo(() => {
    const s = sectionForPath(df?.sections, path);
    return s ? cleanName(s.name) : autoJobName(path, "");
  }, [path, df]);

  useEffect(() => {
    if (!nameTouched) setName(autoName);
  }, [autoName, nameTouched]);

  const create = async () => {
    setBusy(true); setErr(null);
    try {
      if (!path.trim()) throw new Error("pick a section or enter a folder");
      const body = { exp_path: path.trim() };
      // Always sent: the server's own default for a folder is its bare name
      // ("region_R1"), so the prefill (<experiment>_<region> for a data-folder
      // section) has to travel with the request.
      if (name.trim()) body.name = name.trim();
      if (body.exp_path && isS3Path(body.exp_path)) body.fetch_dapi = fetchDapi;
      const job = await jpost("/api/jobs", body);
      await warmOrientationImage(job);
      onCreate(job);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const resume = async (job) => {
    setOpeningId(job.id); setErr(null);
    const warming = warmOrientationImage(job);
    // INPUT_READY opens directly on Orientation, so wait until its image is
    // decoded. Later states can resume immediately while warming in background.
    if (job.state === "INPUT_READY") await warming;
    setOpeningId(null);
    onCreate(job);
  };

  return (
    <>
      <div className="sidebar">
        <h1>New alignment job</h1>
        <div className="muted">
          {(df?.sections || []).length > 0
            ? "Pick a section, or point at an experiment folder."
            : "Point at an experiment folder, on this computer or on S3."}
        </div>

        <DataFolderPanel df={df} setDf={setDf} path={path}
                         onPick={(p) => setPath(p)} />

        <h2>{(df?.sections || []).length > 0
          ? "…or experiment folder" : "Experiment folder"}</h2>
        <input type="text" value={path} placeholder="/path/to/region_R1  or  s3://bucket/.../region_R1"
               onChange={(e) => setPath(e.target.value)}
               style={{ width: "100%" }} />
        {!path.trim() && (
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            A MERSCOPE region folder with <code>cell_metadata.csv</code> and{" "}
            <code>cell_by_gene.csv</code>. It is read where it is; nothing is copied
            except from S3.
          </div>
        )}

        {isS3Path(path) && (
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            Read from S3: the cell tables are fetched to this server when the job
            is created (seconds), and the transcripts follow in the background.
            Files archived in S3 (Glacier / Deep Archive) are reported, not fetched.
            <label style={{ display: "block", marginTop: 6 }}>
              <input type="checkbox" checked={fetchDapi}
                     onChange={(e) => setFetchDapi(e.target.checked)} />{" "}
              Also fetch the DAPI mosaic in the background (~12–63 GB). Without it
              the alignment backgrounds use cell density.
            </label>
          </div>
        )}

        <h2>Job name</h2>
        <input type="text" value={name} placeholder={autoName || "job name"}
               maxLength={NAME_MAX}
               onChange={(e) => {
                 setName(cleanName(e.target.value));
                 setNameTouched(true);
               }}
               style={{ width: "100%" }} />
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          Letters, numbers, space, <span className="kbd">-</span> and{" "}
          <span className="kbd">_</span> only — anything else is dropped as you
          type, because this name becomes a filename. Spaces become underscores
          there.
        </div>
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          Titles the job and names every file you download from it. Leave it as
          it is to use <b>{autoName || "the folder name"}</b>.
        </div>

        <button className="primary" onClick={create} disabled={busy || !path.trim()}
                style={{ width: "100%", padding: 10, fontSize: 14, marginTop: 12 }}>
          {busy ? (isS3Path(path) ? "Fetching from S3…" : "Creating…") : "Create job"}
        </button>
        {err && <div className="result" style={{ color: "#f85149" }}>{err}</div>}
      </div>

      <div className="canvas-wrap" style={{ padding: 24, display: "block", overflow: "auto" }}>
        <h2 style={{ marginTop: 0 }}>Resume a job</h2>
        {jobs.length === 0 && <div className="muted">No previous jobs.</div>}
        {jobs.length > 0 && (
          <div className="jobtools">
            <span className="muted">
              {checked.size ? `${checked.size} of ${jobs.length} selected` : "Select jobs to act on several at once"}
            </span>
            <button onClick={() => download(selected)} disabled={!checked.size}
                    title="Download the report .zip of every selected COMPLETED job">
              ⬇ Download .zip
            </button>
            <button className="danger" onClick={() => remove(selected)}
                    disabled={!checked.size || deleting}
                    title="Permanently delete the selected jobs from the server">
              {deleting ? "Deleting…" : "🗑 Delete"}
            </button>
            {checked.size > 0 && (
              <button onClick={() => { setChecked(new Set()); setNotice(null); }}>Clear</button>
            )}
          </div>
        )}
        {notice && <div className={`jobnotice ${notice.kind}`} role="status">{notice.text}</div>}
        <table className="jobs">
          {jobs.length > 0 && (
            <thead>
              <tr>
                <th style={{ width: 28 }}>
                  <input type="checkbox" aria-label="Select all jobs" checked={allChecked}
                         onChange={() => setChecked(allChecked ? new Set() : new Set(jobs.map((j) => j.id)))} />
                </th>
                <th style={{ width: 1 }}>Actions</th>
                {JOB_COLUMNS.map((col) => (
                  <SortTh key={col.key} col={col} sort={jobSort} onSort={setJobSort} />
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {sortRows(jobs, JOB_COLUMNS, jobSort).map((j) => {
              const done = j.state === COMPLETED;
              const busy = isBusy(j);
              // The row still opens the job; its controls must not, so each one
              // stops the click before it reaches the row.
              const stop = (e) => e.stopPropagation();
              return (
                <tr key={j.id} className="jobrow" onClick={() => resume(j)}>
                  <td onClick={stop}>
                    <input type="checkbox" aria-label={`Select ${j.name || j.id}`}
                           checked={checked.has(j.id)} onChange={() => toggle(j.id)} />
                  </td>
                  {/* Right after the checkbox, so every row's controls sit in one
                      fixed column at the left edge instead of trailing ragged
                      names across a dark table. */}
                  <td className="rowactions" onClick={stop}>
                    <button className="rowbtn" disabled={!done}
                            onClick={() => download([j])}
                            title={done ? "Download this job's report .zip"
                                        : `Not completed (${j.state}): no report yet`}
                            aria-label={`Download report for ${j.name || j.id}`}>⬇</button>
                    <button className="rowbtn danger" disabled={busy || deleting}
                            onClick={() => remove([j])}
                            title={busy ? "Running: wait for it to finish" : "Delete this job"}
                            aria-label={`Delete ${j.name || j.id}`}>🗑</button>
                  </td>
                  <td>{j.name || j.region_key || j.id}</td>
                  <td className="muted">{j.state}</td>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>
                    {openingId === j.id ? "Loading tissue…" : createdText(j)}
                  </td>
                  {/* the folder name under the jobs directory, verbatim */}
                  <td className="muted jobid">{j.id}</td>
                  <NotesCell job={j} onSaved={reloadJobs} />
                </tr>
              );
            })}
          </tbody>
        </table>
        {jobs.length > 0 && (
          <ul className="muted jobsnote">
            <li>
              Jobs are saved on the server in{" "}
              <code>{jobsDir ? `${jobsDir}/<job ID>/` : "pipeline_output/jobs/<job ID>/"}</code>
              , one folder per job, named by its Job ID.
            </li>
            <li>
              <b>Created time</b> follows the server&apos;s time zone
              {serverTz ? ` (${serverTz})` : ""}, not your browser&apos;s: it is the
              same clock that stamps the Job ID, so the two always agree.
            </li>
          </ul>
        )}
      </div>
    </>
  );
}
