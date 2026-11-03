// Shared API helpers for the job-driven pipeline UI.
import { useCallback, useEffect, useRef, useState } from "react";

export const API = "";

export async function jget(url) {
  const r = await fetch(API + url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// CSS transform that reproduces the backend orientation EXACTLY.
// Backend: out = flip(rotate(img, angle)); scipy rotate(angle) is CCW,
// flip 'x' = flipud (vertical), 'y' = fliplr (horizontal).
// CSS rotate(-angle) == CCW by angle; scale() is written left of rotate so the
// rotation is applied first, then the flip (matching flip∘rotate). Verified to
// mean|diff| 0.009 vs the old scipy path.
// `fit` is a uniform shrink factor (see useRotatedFit) folded into the flip scale.
// A uniform scale commutes with the rotation, so the flip∘rotate order above is
// unaffected and the preview still matches the backend orientation exactly.
export function orientTransform(angle, flip, fit = 1) {
  const flx = (flip === "y" ? -1 : 1) * fit;   // fliplr / horizontal
  const fly = (flip === "x" ? -1 : 1) * fit;   // flipud / vertical
  return `scale(${flx}, ${fly}) rotate(${-angle}deg)`;
}

// A CSS transform does not affect layout: the <img> is sized by max-width/height
// FIRST and only then rotated, so at angles away from 0/180 the rotated bounding
// box is wider (and/or taller) than the box that sized it and `overflow: hidden`
// clips the tissue. E.g. a 450x696 image at -104 deg spans 784x605 in a 582x849
// stage -- 202 px of the brain cut off each frame.
//
// This returns the extra uniform shrink needed for the ROTATED box to fit. It only
// ever shrinks (<= 1), so small angles are left exactly as they were.
// 0.5% margin: an exact fit is knife-edge against sub-pixel rounding and would
// still shave a pixel off the edge of the tissue.
const FIT_MARGIN = 0.995;

export function rotatedFitScale(angleDeg, w, h, stageW, stageH) {
  if (!w || !h || !stageW || !stageH) return 1;
  const t = (angleDeg * Math.PI) / 180;
  const c = Math.abs(Math.cos(t)), s = Math.abs(Math.sin(t));
  const bw = w * c + h * s;            // rotated bounding box
  const bh = w * s + h * c;
  return Math.min(1, (stageW / bw) * FIT_MARGIN, (stageH / bh) * FIT_MARGIN);
}

// Measure the stage + the (unrotated) image layout box and keep the fit factor in
// sync with the angle, the image loading, and container resizes. Returns
// [stageRef, imgRef, fit, onImgLoad].
export function useRotatedFit(angle, flip) {
  const stageRef = useRef(null);
  const imgRef = useRef(null);
  const [fit, setFit] = useState(1);

  const measure = useCallback(() => {
    const st = stageRef.current, im = imgRef.current;
    if (!st || !im || !im.offsetWidth || !im.offsetHeight) return;
    // offsetWidth/Height are the pre-transform layout size -- exactly what the
    // rotation is applied to.
    setFit(rotatedFitScale(angle, im.offsetWidth, im.offsetHeight,
                           st.clientWidth, st.clientHeight));
  }, [angle]);

  useEffect(() => { measure(); }, [measure, flip]);
  useEffect(() => {
    const st = stageRef.current;
    if (!st) return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(st);
    return () => ro.disconnect();
  }, [measure]);

  return [stageRef, imgRef, fit, measure];
}

// Pick the base-image resolution for the orientation preview from the container
// width and devicePixelRatio, so the rotated image never has to be upscaled by
// CSS (which is what makes edges look blocky). Clamped and rounded so we don't
// request an oversized PNG; call this ONCE per step so the <img> src stays
// constant and dragging the slider triggers no new network requests.
export function pickMaxPx(containerPx) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  // fall back to ~60% of the viewport if we can't measure the container yet.
  const stageW = containerPx || (window.innerWidth || 1200) * 0.6;
  const px = stageW * 0.85 * dpr;            // image is capped at ~82% of stage
  return Math.max(800, Math.min(2000, Math.round(px / 200) * 200));
}

export async function jpost(url, body) {
  const r = await fetch(API + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.json();
}

// Ordered pipeline states -> friendly step labels.
// Currently UNUSED: App.jsx renders the rail from its own STEPS array. Kept in
// sync with it anyway, because two lists of the same labels that disagree is
// how a stale one ends up on screen the day someone imports this.
export const STEPS = [
  { key: "INPUT_READY", label: "Input" },
  { key: "ORIENTATION_CONFIRMED", label: "Orientation" },
  { key: "AP_CONFIRMED", label: "Reference level" },
  { key: "AUTO_REGISTRATION_DONE", label: "Registration" },
  { key: "MANUAL_CURATION_CONFIRMED", label: "Curation" },
  { key: "COMPLETED", label: "Results" },
];

// Which step index a job is currently sitting at / editing.
export function stepIndex(state) {
  const order = [
    "INPUT_READY", "ORIENTATION_CONFIRMED", "AP_CONFIRMED",
    "AUTO_REGISTRATION_DONE", "FINALIZING", "MANUAL_CURATION_CONFIRMED", "COMPLETED",
  ];
  const i = order.indexOf(state);
  if (state === "FINALIZING" || state === "MANUAL_CURATION_CONFIRMED") return 4;
  if (state === "COMPLETED") return 5;
  return i < 0 ? 0 : Math.min(i, 5);
}

// ---- job name ------------------------------------------------------------
// A job name ends up in a Content-Disposition header and in zip entry names, so
// the field only accepts what is safe there: letters, digits, space, dash and
// underscore. Applied to what the user types AND to the prefilled folder name,
// because a prefill that broke the field's own rule would be indefensible.
//
// The server sanitises again in web/jobs.safe_name -- that is the boundary.
// This is so the user sees the rule while typing rather than discovering it in
// a downloaded filename.
export const NAME_ALLOWED = /[^A-Za-z0-9 _-]+/g;
export const NAME_MAX = 120;

export function cleanName(v) {
  return String(v == null ? "" : v).replace(NAME_ALLOWED, "").slice(0, NAME_MAX);
}

/** The name a job would get with the field left alone: the last path segment,
 *  or the region key, reduced to the same allowed set. An s3:// path gets the
 *  key the server derives (web/s3source.auto_key): every region folder is
 *  "region_R1", so the experiment's timestamp has to be in it. */
export function autoJobName(path, regionKey) {
  const p = String(path || "").trim().replace(/\/+$/, "");
  if (isS3Path(p)) return s3AutoKey(p);
  return cleanName(p ? (p.split("/").pop() || "") : (regionKey || ""));
}

export function isS3Path(p) {
  return /^s3:\/\//i.test(String(p || "").trim());
}

/** `s3://b/.../202401020900_xyz/region_R1/` -> `s3_202401020900_R1`. */
export function s3AutoKey(url) {
  const parts = String(url).trim().replace(/^s3:\/\/[^/]+\/?/i, "")
    .split("/").filter(Boolean);
  if (!parts.length) return "";
  let region = parts[parts.length - 1];
  const exp = parts.length > 1 ? parts[parts.length - 2] : "";
  const ts = exp ? exp.split("_")[0] : "";
  if (region.startsWith("region_")) region = region.slice("region_".length);
  return ["s3", ts, region].filter(Boolean).join("_").replace(/[^A-Za-z0-9_-]/g, "_");
}

// Cells that sampled the atlas PAST THE END of its volume (`atlas_overhang` on
// a registration or finalize result; see planes.atlas_overhang). They carry no
// region because the reference stops there, which reads very differently from
// "the fit missed" -- so every step words it the same way, from here.
// Returns null when there is nothing to say, which is the usual coronal case.
export function overhangSummary(o) {
  if (!o || !o.n) return null;
  const pct = (f) => (f > 0 && f < 0.001 ? "<0.1%" : `${(f * 100).toFixed(1)}%`);
  return {
    n: o.n,
    pct: pct(o.frac),
    ends: (o.ends || []).map((e) => `${e.end} ${pct(e.frac)}`).join(", "),
  };
}
