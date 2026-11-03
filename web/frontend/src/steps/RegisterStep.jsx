import React, { useEffect, useRef, useState } from "react";
import { jget, jpost, overhangSummary } from "../jobapi";

export default function RegisterStep({ job, onNext, onUpdate, autoAdvance = true }) {
  const done = (job.register?.status === "done") ||
    ["AUTO_REGISTRATION_DONE", "MANUAL_CURATION_CONFIRMED", "FINALIZING", "COMPLETED"].includes(job.state);
  const [running, setRunning] = useState(job.register?.status === "running");
  const [log, setLog] = useState([]);
  const [err, setErr] = useState(job.state === "FAILED" ? job.error : null);
  const logRef = useRef(null);
  const autoStartRef = useRef(false);
  const reg = job.register?.result || {};
  const overhang = overhangSummary(reg.atlas_overhang);
  const apAuditMissing = done && reg.sampled_ap_idx == null;
  const apMismatch = reg.sampled_ap_idx != null && reg.sampled_ap_idx !== job.ap_idx;
  const blocked = apAuditMissing || apMismatch;

  // Warm the relatively large plane-aligned DAPI. Curation requests the exact
  // same versioned URL, so the transition normally becomes a memory/disk-cache
  // hit instead of a blank wait. This effect is declared BEFORE the auto-advance
  // below and so runs first on the same render, which means the image request is
  // already in flight by the time Curation mounts -- there is no longer a pause
  // on this step for it to load during.
  useEffect(() => {
    if (!done || !reg.bundle_path) return;
    const img = new Image();
    img.src = `/api/dapi.png?path=${encodeURIComponent(reg.bundle_path)}`
      + `&v=${encodeURIComponent(job.updated || "")}`;
  }, [done, reg.bundle_path, job.updated]);

  useEffect(() => {
    if (!running) return undefined;
    let alive = true;
    const tick = async () => {
      try {
        const res = await jget(`/api/jobs/${job.id}/register_status`);
        if (!alive) return;
        setLog(res.log || []);
        if (res.status === "done") {
          setRunning(false);
          onUpdate(res.job);
        } else if (res.status === "failed") {
          setRunning(false);
          setErr(res.job.error || "registration failed");
        }
      } catch (e) { /* keep polling */ }
    };
    tick();
    const h = setInterval(tick, 2500);
    return () => { alive = false; clearInterval(h); };
  }, [running, job.id]);

  // AP confirmation now means "run registration". Keep this guard here as
  // well as in APStep so a refreshed/stale tab that lands on AP_CONFIRMED still
  // starts immediately instead of exposing a second button.
  useEffect(() => {
    if (done || running || job.state !== "AP_CONFIRMED" ||
        job.register?.status === "running" || autoStartRef.current) return;
    autoStartRef.current = true;
    setErr(null); setLog([]);
    jpost(`/api/jobs/${job.id}/register`, {})
      .then(() => setRunning(true))
      .catch((e) => {
        autoStartRef.current = false;
        setErr(String(e.message || e));
      });
  }, [done, running, job.id, job.state, job.register?.status]);

  // Registration finishes in about six seconds -- measured at 6-11s across every
  // run on disk -- so a stop whose only control is "Continue" is pure friction:
  // by the time it paints, the work is already done. It advances itself instead,
  // and Curation carries the coverage summary so nothing is read-and-lost.
  //
  // Not when `blocked`: an AP mismatch or a legacy bundle is exactly the case a
  // human has to look at. Not when `autoAdvance` is false either -- that means
  // the user clicked the rail to come back here, and bouncing them forward again
  // would make this step unreadable.
  useEffect(() => {
    if (!autoAdvance || !done || blocked) return;
    onNext(job);
    // onNext is re-created every render by the parent, so it is deliberately not
    // a dependency; this must fire on the done/blocked transition only.
  }, [autoAdvance, done, blocked]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  const start = async () => {
    setErr(null); setLog([]);
    try {
      await jpost(`/api/jobs/${job.id}/register`, {});
      setRunning(true);
    } catch (e) { setErr(String(e.message || e)); }
  };

  return (
    <>
      <div className="sidebar">
        <h1>Automatic registration</h1>
        <div className="muted">
          Fits the 3D atlas to the tissue at the chosen AP level, then reads an
          Allen region off the atlas for every cell. It takes a few seconds and
          hands straight over to curation.
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <span className="badge">selected AP {job.ap_idx ?? "—"}</span>
          {reg.sampled_ap_idx != null && (
            <span className="badge">annotation AP {reg.sampled_ap_idx}</span>
          )}
        </div>

        {!done && !running && err && (
          <button className="primary" onClick={start}
                  style={{ width: "100%", padding: 10, fontSize: 14, marginTop: 12 }}>
            Retry registration
          </button>
        )}
        {!done && !err && (
          <div className="muted" style={{ marginTop: 12 }}>
            {running ? "Running…" : "Starting registration…"}
          </div>
        )}

        {done && (
          <div className="result">
            <div className="row">
              <span className="badge">{(reg.coverage * 100).toFixed(1)}% labeled</span>
              {reg.n_cells > 0 && (
                <span className="badge">
                  {(reg.n_mapped || 0).toLocaleString()} of{" "}
                  {reg.n_cells.toLocaleString()} cells
                </span>
              )}
            </div>
            <div className="muted" style={{ marginTop: 8 }}>
              Labelled means the cell landed inside an annotated Allen region on
              this plane. The rest fell outside the annotation — off the section
              edge, or on unannotated tissue — and carry no region.
            </div>
            {overhang && (
              <div className="muted" style={{ marginTop: 8 }}>
                <b>{overhang.n.toLocaleString()} cells ({overhang.pct})</b> lie past
                the end of the Allen atlas ({overhang.ends}) and are among the
                unlabelled. The reference stops there: this is a limit of the
                atlas, not of the fit
                {(job.orientation?.plane || "coronal") === "sagittal"
                  ? " — on a sagittal section the AP axis runs across the image, and the Allen volume has no margin at either AP end."
                  : "."}
              </div>
            )}
            <div className="muted" style={{ marginTop: 8 }}>
              This is the <b>automatic</b> fit and nothing else. Region boundaries
              are corrected by hand in step 5, Curation.
            </div>
            {reg.tilt_deg != null && (
              <div className="muted" style={{ marginTop: 8 }}>
                tilt {reg.tilt_deg.toFixed(1)}°
              </div>
            )}
            {reg.sampled_ap_idx != null && reg.sampled_ap_idx !== job.ap_idx && (
              <div style={{ color: "#f85149", marginTop: 8 }}>
                AP mismatch: selected {job.ap_idx}, annotation {reg.sampled_ap_idx}.
                Re-run registration before curation.
              </div>
            )}
            {apAuditMissing && (
              <div style={{ color: "#f85149", marginTop: 8 }}>
                Legacy bundle: the actual annotation AP was not recorded.
                Re-run registration before curation.
              </div>
            )}
            <button className="primary" onClick={() => onNext(job)} disabled={blocked}
                    style={{ width: "100%", padding: 10, fontSize: 14, marginTop: 12 }}>
              Continue to curation
            </button>
          </div>
        )}
        {err && <div className="result" style={{ color: "#f85149" }}>{err}</div>}
      </div>

      <div className="canvas-wrap" style={{ display: "block", padding: 16, overflow: "hidden" }}>
        {done && reg.fig_path ? (
          <img src={`/api/download?path=${encodeURIComponent(reg.fig_path)}`} alt="registration result"
               style={{ maxWidth: "100%", maxHeight: "90vh", display: "block", margin: "0 auto" }} />
        ) : (
          <pre ref={logRef} className="log">{log.join("\n") || "No output yet."}</pre>
        )}
      </div>
    </>
  );
}
