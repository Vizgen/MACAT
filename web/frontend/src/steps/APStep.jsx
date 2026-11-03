import React, { useEffect, useMemo, useState } from "react";
import { API, jpost, orientTransform, pickMaxPx, useRotatedFit } from "../jobapi";
import { knownLevelText } from "../jobActions";

export default function APStep({ job, onNext }) {
  const o = job.orientation || { angle: 0, flip: "none" };
  const plane = o.plane || "coronal";
  // null until /api/atlas_meta says where this plane's slider starts: a job's
  // own index means a different slice at another atlas resolution, so even a
  // stored ap_idx is not usable before the backend has carried it over.
  const [idx, setIdx] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [atlasMeta, setAtlasMeta] = useState(null);
  // shrink the tissue so its ROTATED bounding box fits the stage (the orientation
  // angle is already fixed by step 2, so this is computed once per resize)
  const [stageRef, imgRef, fit, onFit] = useRotatedFit(o.angle, o.flip);

  // atlas is genuinely different per AP index (server-rendered); debounce it.
  const [dIdx, setDIdx] = useState(idx);
  useEffect(() => {
    const h = setTimeout(() => setDIdx(idx), 180);
    return () => clearTimeout(h);
  }, [idx]);
  useEffect(() => {
    fetch(`${API}/api/atlas_meta`)
      .then((r) => { if (!r.ok) throw new Error(`atlas_meta: ${r.status}`); return r.json(); })
      .then(setAtlasMeta)
      .catch((e) => setErr(`Could not load the atlas slider: ${e.message || e}`));
  }, []);

  // Bounds, start and micron scale all come from the backend (slider() in
  // allen_alignment/planes.py), so nothing here knows the atlas resolution.
  const pmeta = atlasMeta?.planes?.[plane];
  const lo = pmeta?.lo, hi = pmeta?.hi;
  const axis = pmeta?.axis || "AP";
  const check = job.atlas_check;
  // Allen Brain Cell Atlas sections (positive controls): the published
  // registration's level at this server's resolution, from the answer key.
  const known = job.known_level;
  useEffect(() => {
    if (!pmeta || idx !== null) return;
    // A level already picked wins; else a known-answer section opens on its
    // known level; else the plane's default.
    const start = check?.ap_idx_here ?? known?.idx ?? pmeta.default_idx;
    setIdx(Math.max(pmeta.lo, Math.min(pmeta.hi, start)));
    setDIdx(Math.max(pmeta.lo, Math.min(pmeta.hi, start)));
  }, [pmeta]);
  const um = pmeta && idx !== null ? pmeta.origin_um + idx * pmeta.step_um : null;
  // the job's slice came from another resolution and was carried over by microns
  const carried = check && check.ap_res_um != null
    && check.ap_res_um !== check.server_res_um;
  const nudge = (delta) => setIdx((v) => Math.max(lo, Math.min(hi, v + delta)));

  const atlasSrc = `${API}/api/atlas.png?idx=${dIdx}&plane=${plane}`;
  // tissue base fetched once at display resolution, rotated via CSS (no refetch).
  const baseSrc = useMemo(
    () => `${API}/api/jobs/${job.id}/base.png?max_px=${pickMaxPx()}`,
    [job.id],
  );

  const confirm = async () => {
    setBusy(true); setErr(null);
    try {
      const updated = await jpost(`/api/jobs/${job.id}/ap`, { ap_idx: idx });
      const running = await jpost(`/api/jobs/${job.id}/register`, {});
      onNext(running);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="sidebar">
        <h1>{axis} level</h1>
        <div className="muted">Slide the atlas until its landmarks (hippocampus in red, ventricles in cyan) match the tissue on the right.</div>

        <h2>Atlas {axis} index</h2>
        <div className="row">
          <button className="nudge" aria-label={`Previous ${axis} slice`}
                  onClick={() => nudge(-1)} disabled={idx === null}>−</button>
          <input type="range" min={lo ?? 0} max={hi ?? 0} step={1} value={idx ?? 0}
                 disabled={idx === null}
                 onChange={(e) => setIdx(+e.target.value)} style={{ flex: 1 }} />
          <button className="nudge" aria-label={`Next ${axis} slice`}
                  onClick={() => nudge(1)} disabled={idx === null}>+</button>
          <span className="badge">{idx ?? "…"}</span>
        </div>
        {um !== null && (
          <div className="muted">{um.toFixed(1)} µm from the atlas centre</div>
        )}
        {known && (
          <div className="result">
            Allen Brain Cell Atlas section: its published registration puts it
            at <b>{knownLevelText(known)}</b> ({known.res_um} µm atlas). The slider
            starts there.{" "}
            {idx !== known.idx && (
              <button className="nudge" style={{ width: "auto", padding: "0 8px" }}
                      onClick={() => setIdx(known.idx)}>Go to {known.idx}</button>
            )}
          </div>
        )}
        {carried && (
          <div className="result">
            This job's level was picked on the {check.ap_res_um} µm atlas. It has
            been carried over to the nearest {check.server_res_um} µm slice by
            position; check it against the tissue before re-running registration.
          </div>
        )}

        {atlasMeta && (
          <div className="provenance">
            <b>Atlas source</b><br />
            Allen Mouse Brain atlas · {atlasMeta.resolution_um?.[0] ?? 50} µm ·
            BrainGlobe dataset v{atlasMeta.dataset_version}<br />
            <a href="https://doi.org/10.1016/j.cell.2020.04.007" target="_blank" rel="noreferrer">
              Wang et al., 2020
            </a> · dataset dated {atlasMeta.dataset_date}
          </div>
        )}

        <button className="primary" onClick={confirm} disabled={busy || idx === null}
                style={{ width: "100%", padding: 10, fontSize: 14, marginTop: 12 }}>
          {busy ? "Starting registration…" : "Run registration"}
        </button>
        {err && <div className="result" style={{ color: "#f85149" }}>{err}</div>}
      </div>

      <div className="canvas-wrap ap-stage">
        <figure className="stage-box">
          <div className="orient-stage">
            {dIdx !== null && <img className="orient-img" src={atlasSrc} alt="atlas" />}
          </div>
          <figcaption className="muted">CCF atlas · {plane} · {axis} {idx ?? ""}</figcaption>
        </figure>
        <figure className="stage-box">
          <div className="orient-stage" ref={stageRef}>
            <img className="orient-img" src={baseSrc} alt="tissue" draggable={false}
                 ref={imgRef} onLoad={onFit}
                 style={{ transform: orientTransform(o.angle, o.flip, fit) }} />
          </div>
          <figcaption className="muted">tissue</figcaption>
        </figure>
      </div>
    </>
  );
}
