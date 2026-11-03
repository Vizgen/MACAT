import React, { useMemo, useState } from "react";
import { API, jpost, orientTransform, pickMaxPx, useRotatedFit } from "../jobapi";

// The backend orientation only applies vertical/horizontal flips, so we expose
// exactly those (keeps the live preview identical to what registration uses).
const FLIPS = [["none", "none"], ["x", "flip vertical"], ["y", "flip horizontal"]];

export default function OrientStep({ job, onNext }) {
  const init = job.orientation || {};
  const [angle, setAngle] = useState(init.angle ?? 0);
  const [flip, setFlip] = useState(init.flip ?? "none");
  // Which Allen axis this section was cut along. It belongs here rather than in
  // the AP step because it decides WHAT that step's slider indexes -- 264
  // coronal slices versus 228 sagittal ones -- and because changing it
  // invalidates the same downstream work a rotation does.
  const [plane, setPlane] = useState(init.plane ?? "coronal");
  // A coronal section that is one half of the brain. The automatic fit cannot
  // tell half a brain from a whole small one, so the user says so: "left" /
  // "right" is the half of the slice AS DISPLAYED here and in step 3.
  const [hemi, setHemi] = useState(init.hemisection ?? "whole");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const nudge = (delta) => setAngle((v) => Math.max(-180, Math.min(180, v + delta)));
  // keep the whole tissue visible while the angle is being dragged: without this
  // the rotated bounding box outgrows the stage and `overflow: hidden` clips it
  const [stageRef, imgRef, fit, onFit] = useRotatedFit(angle, flip);

  // fetched ONCE at a resolution matched to the display, then rotated live via
  // CSS. useMemo([]) keeps the src constant so slider drags make no requests.
  const baseSrc = useMemo(
    () => `${API}/api/jobs/${job.id}/base.png?max_px=${pickMaxPx()}`,
    [job.id],
  );

  const confirm = async () => {
    setBusy(true); setErr(null);
    try {
      const updated = await jpost(`/api/jobs/${job.id}/orientation`,
                                  { angle, flip, plane,
                                    hemisection: plane === "coronal" ? hemi : "whole" });
      onNext(updated);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="sidebar">
        <h1>Orientation</h1>
        <div className="muted">
          Pick the plane the section was cut in, then rotate and flip the tissue
          so it sits upright, dorsal up, matching the atlas view beside it.
        </div>
        {/* The section's answer key (expected.json) pre-filled these controls;
            say so, so the user knows where the values came from. */}
        {job.answer_orientation && (
          <div className="result" style={{ fontSize: 12 }}>
            Pre-filled from this section&apos;s answer key (<code>expected.json</code>).
            Check it against the tissue before confirming.
          </div>
        )}

        <h2>Section plane</h2>
        <div className="seg">
          {[["coronal", "Coronal"], ["sagittal", "Sagittal"]].map(([v, label]) => (
            <button key={v} className={plane === v ? "active" : ""}
                    onClick={() => { setPlane(v); if (v !== "coronal") setHemi("whole"); }}>
              {label}
            </button>
          ))}
        </div>
        <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
          {plane === "coronal"
            ? "The atlas is sliced front to back; step 3 picks an AP level."
            : "The atlas is sliced left to right; step 3 picks an ML level. "
              + "Rotate below so anterior is one side and dorsal is up."}
        </div>

        {plane === "coronal" && (
          <>
            <h2>Section</h2>
            <div className="seg">
              {[["whole", "Whole"], ["left", "Left half"], ["right", "Right half"]].map(([v, label]) => (
                <button key={v} className={hemi === v ? "active" : ""}
                        onClick={() => setHemi(v)}>{label}</button>
              ))}
            </div>
            <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
              {hemi === "whole"
                ? "Both hemispheres are on the slide."
                : `Only the ${hemi} half of the brain, as it looks once rotated `
                  + "dorsal up: the midline is the tissue's "
                  + (hemi === "left" ? "right" : "left") + " edge. Registration "
                  + "then fits it to that half of the atlas slice."}
            </div>
          </>
        )}

        <h2>Rotation</h2>
        <div className="row">
          <button className="nudge" aria-label="Rotate one degree counter-clockwise" onClick={() => nudge(-1)}>−</button>
          <input type="range" min={-180} max={180} step={1} value={angle}
                 onChange={(e) => setAngle(+e.target.value)} style={{ flex: 1 }} />
          <button className="nudge" aria-label="Rotate one degree clockwise" onClick={() => nudge(1)}>+</button>
          <span className="badge">{angle}°</span>
        </div>

        <h2>Flip</h2>
        <select value={flip} onChange={(e) => setFlip(e.target.value)} style={{ width: "100%" }}>
          {FLIPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>

        <button className="primary" onClick={confirm} disabled={busy}
                style={{ width: "100%", padding: 10, fontSize: 14, marginTop: 12 }}>
          {busy ? "Saving…" : "Confirm orientation"}
        </button>
        {err && <div className="result" style={{ color: "#f85149" }}>{err}</div>}
      </div>

      <div className="canvas-wrap" style={{ padding: 0 }}>
        <div className="orient-stage guides" ref={stageRef}>
          <img className="orient-img" src={baseSrc} alt="tissue" draggable={false}
               ref={imgRef} onLoad={onFit}
               style={{ transform: orientTransform(angle, flip, fit) }} />
        </div>
      </div>
    </>
  );
}
