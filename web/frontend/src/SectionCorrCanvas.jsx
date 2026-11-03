// Whole-section expression against the Allen reference at the same AP.
//
// One point per shared gene: reference mean counts/cell on x, this section's on
// y, both log10. The identity line is the thing to read against -- a gene above
// it is detected more here than in the reference, below it less -- so the axes
// share one square domain and the line sits at 45 degrees.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { toLog, domain, ticks, nearest } from "./sectionCorr";

const DOT = 2.6;
const TAU = Math.PI * 2;

export default function SectionCorrCanvas({ data, height = 380 }) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const viewRef = useRef({ scale: 1, tx: 0, ty: 0 });
  const panRef = useRef(null);
  const drawRef = useRef(() => {});
  const [hover, setHover] = useState(null);

  const points = data?.points || [];
  const dom = useMemo(() => domain(points), [points]);
  const tk = useMemo(() => ticks(dom), [dom]);

  const PAD = { l: 52, r: 12, t: 10, b: 34 };

  // data -> screen, including the current pan/zoom.
  // ONE scale `k` for both axes, and the plotted square centred in the inner
  // box: that is what keeps the identity line at 45 degrees, which is the only
  // angle a reader can judge agreement against.
  const placeRef = useRef(() => [0, 0]);
  function makePlace(w, h) {
    const v = viewRef.current;
    const iw = w - PAD.l - PAD.r, ih = h - PAD.t - PAD.b;
    const span = dom.hi - dom.lo;
    const k = Math.min(iw, ih) / span;
    const ox = PAD.l + (iw - span * k) / 2;
    const oy = PAD.t + (ih - span * k) / 2;
    return (p) => {
      const x = ox + (toLog(p.r) - dom.lo) * k;
      const y = oy + span * k - (toLog(p.q) - dom.lo) * k;   // y grows downward
      return [v.tx + x * v.scale, v.ty + y * v.scale];
    };
  }

  drawRef.current = function draw() {
    const cv = canvasRef.current;
    if (!cv || !dom || !points.length) return;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); }
    if (cv.height !== Math.round(h * dpr)) { cv.height = Math.round(h * dpr); }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const place = makePlace(w, h);
    placeRef.current = place;
    const at = (lr, lq) => place({ r: Math.pow(10, lr) - 1e-4, q: Math.pow(10, lq) - 1e-4 });

    // identity line first, under the data
    ctx.save();
    ctx.beginPath();
    const a = at(dom.lo, dom.lo), b = at(dom.hi, dom.hi);
    ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
    ctx.strokeStyle = "#3a4653"; ctx.lineWidth = 1; ctx.setLineDash([5, 5]);
    ctx.stroke(); ctx.setLineDash([]);
    ctx.restore();

    // decade gridlines + labels
    ctx.font = "10px system-ui, sans-serif";
    ctx.fillStyle = "#8b949e"; ctx.strokeStyle = "#1c2530"; ctx.lineWidth = 1;
    for (const e of tk) {
      const p1 = at(e, dom.lo), p2 = at(e, dom.hi);
      ctx.beginPath(); ctx.moveTo(p1[0], p1[1]); ctx.lineTo(p2[0], p2[1]); ctx.stroke();
      const q1 = at(dom.lo, e), q2 = at(dom.hi, e);
      ctx.beginPath(); ctx.moveTo(q1[0], q1[1]); ctx.lineTo(q2[0], q2[1]); ctx.stroke();
      ctx.textAlign = "center"; ctx.fillText(`1e${e}`, p1[0], h - PAD.b + 14);
      ctx.textAlign = "right"; ctx.fillText(`1e${e}`, PAD.l - 6, q1[1] + 3);
    }
    ctx.textAlign = "center";
    ctx.fillStyle = "#8b949e";
    ctx.fillText("Allen reference · mean counts per cell", (w + PAD.l) / 2, h - 4);
    ctx.save();
    ctx.translate(12, (h - PAD.b + PAD.t) / 2); ctx.rotate(-Math.PI / 2);
    ctx.fillText("this section · mean counts per cell", 0, 0);
    ctx.restore();

    ctx.fillStyle = "#58a6ff";
    for (const p of points) {
      const [x, y] = place(p);
      ctx.beginPath(); ctx.moveTo(x + DOT, y); ctx.arc(x, y, DOT, 0, TAU); ctx.fill();
    }
    if (hover) {
      const [x, y] = place(hover);
      ctx.beginPath(); ctx.arc(x, y, DOT + 3, 0, TAU);
      ctx.strokeStyle = "#f0f6fc"; ctx.lineWidth = 1.5; ctx.stroke();
    }
  };

  const fit = () => { viewRef.current = { scale: 1, tx: 0, ty: 0 }; drawRef.current(); };

  useEffect(() => { fit(); }, [dom]);
  useEffect(() => { drawRef.current(); }, [hover]);
  useEffect(() => {
    const on = () => drawRef.current();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, [dom]);

  // native + non-passive: React's wheel listener is passive, so preventDefault
  // in a JSX handler is ignored and the page scrolls behind the zoom
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return undefined;
    const handler = (e) => {
      e.preventDefault();
      const rect = cv.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const v = viewRef.current;
      const k = Math.exp(-e.deltaY * 0.0015);
      v.tx = mx - (mx - v.tx) * k; v.ty = my - (my - v.ty) * k; v.scale *= k;
      drawRef.current();
    };
    cv.addEventListener("wheel", handler, { passive: false });
    return () => cv.removeEventListener("wheel", handler);
  }, []);

  if (!data) return <div className="muted">Loading correlation …</div>;
  if (!data.available) return <div className="muted">{data.reason}</div>;
  if (!points.length) return <div className="muted">No shared genes to plot.</div>;

  return (
    <div ref={wrapRef} style={{ position: "relative", height,
                                background: "#0b0f14", borderRadius: 8,
                                border: "1px solid #1c2530", overflow: "hidden" }}>
      <canvas ref={canvasRef}
              style={{ width: "100%", height: "100%", display: "block",
                       cursor: panRef.current ? "grabbing" : "crosshair",
                       touchAction: "none" }}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                panRef.current = { x0: e.clientX, y0: e.clientY,
                                   tx0: viewRef.current.tx, ty0: viewRef.current.ty };
                setHover(null);
              }}
              onPointerUp={() => { panRef.current = null; }}
              onPointerCancel={() => { panRef.current = null; }}
              onPointerLeave={() => { panRef.current = null; setHover(null); }}
              onPointerMove={(e) => {
                const p = panRef.current;
                if (p) {
                  const v = viewRef.current;
                  v.tx = p.tx0 + (e.clientX - p.x0);
                  v.ty = p.ty0 + (e.clientY - p.y0);
                  drawRef.current();
                  return;
                }
                const rect = e.currentTarget.getBoundingClientRect();
                const mx = e.clientX - rect.left, my = e.clientY - rect.top;
                const hit = nearest(points, placeRef.current, mx, my);
                setHover(hit);
                if (hit) setHover({ ...hit, mx, my });
              }} />
      <button className="badge" onClick={fit}
              title="Back to the full range"
              style={{ position: "absolute", right: 8, top: 8, cursor: "pointer",
                       font: "inherit", fontSize: 11, background: "#0b0f14cc" }}>
        reset view
      </button>
      {hover && hover.mx != null && (
        <div style={{ position: "absolute", left: Math.min(hover.mx + 12, 9999),
                      top: hover.my + 12, pointerEvents: "none",
                      background: "#11161d", border: "1px solid var(--border)",
                      borderRadius: 6, padding: "4px 8px", fontSize: 11,
                      whiteSpace: "nowrap" }}>
          <b>{hover.g}</b>{" "}
          <span className="muted">
            this {hover.q.toFixed(3)} · ref {hover.r.toFixed(3)}
          </span>
        </div>
      )}
    </div>
  );
}
