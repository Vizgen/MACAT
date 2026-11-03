import React, { useState } from "react";
import InputStep from "./steps/InputStep";
import OrientStep from "./steps/OrientStep";
import APStep from "./steps/APStep";
import RegisterStep from "./steps/RegisterStep";
import CurationStep from "./steps/CurationStep";
import ResultsStep from "./steps/ResultsStep";

// The rail names the step the same way whatever plane the section was cut in.
// It used to say "AP level", which is the CORONAL axis: on a sagittal job the
// rail said AP while the panel it pointed at said "ML level". The panel stays
// plane-specific -- it is naming the axis you are actually sliding, and
// APStep renders `{axis} level` from the plane metadata -- but the rail is a
// table of contents and has no business asserting an axis.
const STEPS = ["Input", "Orientation", "Reference level", "Registration",
               "Curation", "Results"];

// view index the job is currently sitting at, and the furthest reachable step.
const CUR = {
  INPUT_READY: 1, ORIENTATION_CONFIRMED: 2, AP_CONFIRMED: 3,
  AUTO_REGISTRATION_DONE: 3, FINALIZING: 4, MANUAL_CURATION_CONFIRMED: 4, COMPLETED: 5,
};
const MAX = {
  INPUT_READY: 1, ORIENTATION_CONFIRMED: 2, AP_CONFIRMED: 3,
  AUTO_REGISTRATION_DONE: 4, FINALIZING: 4, MANUAL_CURATION_CONFIRMED: 4, COMPLETED: 5,
};

export default function App() {
  const [job, setJob] = useState(null);
  const [view, setView] = useState(0);
  const [dirty, setDirty] = useState(false); // Curation has unconfirmed edits
  // True once the user has steered with the rail. Registration advances itself
  // when it finishes, which is right on the way through and wrong when someone
  // has deliberately clicked back to step 4 to read the coverage or the log --
  // without this they would be bounced straight out again.
  const [pinned, setPinned] = useState(false);

  const goto = (updated) => {
    setPinned(false);
    setJob(updated);
    setView(CUR[updated.state] ?? 1);
  };
  const restart = () => {
    setJob(null); setView(0); setDirty(false); setPinned(false);
  };
  const maxStep = job ? (MAX[job.state] ?? 1) : 0;

  // guard rail navigation: leaving Curation (view 4) with unconfirmed edits must
  // not silently show a stale Results — force a confirm/discard decision first.
  const navTo = (i) => {
    if (i === view) return;
    if (view === 4 && dirty && i !== 4) {
      const ok = window.confirm(
        "You have unconfirmed curation edits. Leave without confirming? " +
        "Your edits will be discarded and Results will show the previous result.");
      if (!ok) return;
      setDirty(false);
    }
    setPinned(true);
    setView(i);
  };

  const rail = (
    <nav className="rail">
      <div className="brand">MACAT</div>
      {STEPS.map((label, i) => {
        const reachable = i === 0 || i <= maxStep;
        const cls = ["railstep",
          i === view ? "active" : "",
          reachable && i < view ? "done" : "",
          reachable ? "" : "todo"].join(" ").trim();
        return (
          <button key={label} className={cls} disabled={!reachable}
                  onClick={() => reachable && navTo(i)}>
            <span className="num">{i + 1}</span>{label}
          </button>
        );
      })}
      {job && <div className="railjob muted">{job.name}<br />{job.state}</div>}
    </nav>
  );

  // A job registered on another atlas resolution: the server refuses its bundle
  // (409), so the steps that read it would only fail piecemeal. Say why once,
  // keep the archive that was already written, and point at the fix.
  const stale = job && job.atlas_check && job.atlas_check.ok === false;

  let body;
  if (stale && view >= 3) {
    body = (
      <div className="sidebar">
        <h1>Re-registration needed</h1>
        <div className="result">{job.atlas_check.reason}</div>
        <div className="muted" style={{ marginTop: 8 }}>
          Its curation and results cannot be opened at this resolution. The
          reference level carries over by position; check it, then re-run
          registration. That discards this job's curation.
        </div>
        <button className="primary" style={{ width: "100%", padding: 10, marginTop: 12 }}
                onClick={() => { setPinned(true); setView(2); }}>
          Re-pick reference level
        </button>
        {job.state === "COMPLETED" && (
          <a className="primary" href={`/api/jobs/${job.id}/report.zip`}
             style={{ display: "block", textAlign: "center", marginTop: 8 }}>
            Download the existing report (.zip)
          </a>
        )}
      </div>
    );
  } else if (view === 0 || !job) {
    body = <InputStep onCreate={goto} />;
  } else if (view === 1) {
    body = <OrientStep job={job} onNext={goto} />;
  } else if (view === 2) {
    body = <APStep job={job} onNext={goto} />;
  } else if (view === 3) {
    body = <RegisterStep job={job} onUpdate={setJob} autoAdvance={!pinned}
                         onNext={() => setView(4)} />;
  } else if (view === 4) {
    body = <CurationStep job={job} onDirty={setDirty}
                         onComplete={(u) => { setDirty(false); setJob(u); setView(5); }} />;
  } else {
    body = <ResultsStep job={job} onRestart={restart} />;
  }

  return (
    <div className="app">
      {rail}
      <div className="content">{body}</div>
    </div>
  );
}
