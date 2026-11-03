// Interactive per-cell panel: pan, zoom, hover a cell for its region, and toggle
// regions off. A prototype alongside the static figure, not a replacement -- the
// .zip is still what goes in a report.
//
// Plain 2-D canvas rather than a plotting library. 85,000 points is well within
// what canvas handles, and the alternative measured at roughly a megabyte gzipped
// of dependency for one panel type, against a 229 KB app.
//
// The draw loop groups points BY REGION so `fillStyle` is set ~175 times per
// frame instead of 85,000 times. Setting it per point is what makes a naive
// canvas scatter slow, not the number of points.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { buildIndex, nearest } from "./cellindex";

const DOT = 1.6;           // point size in CSS pixels at zoom 1
const TAU = Math.PI * 2;
const ROUND_ABOVE_PX = 2;  // below this, a circle and a square are the same pixels
// Matches _legend_rows(limit=16) in figures.py, so the interactive pills and the
// static legend name the same regions. The rest are reachable behind "show all":
// every region IS drawn, and capping the pills at 16 silently made 59% of the
// cells on a measured job untoggleable, which defeats the point of the toggle.
const PILLS = 16;
const HOVER_PX = 6;        // how close the cursor must be to name a cell

function decode(b64, Type) {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return new Type(buf.buffer);
}

export default function CellCanvas({ data, palette = "allen", height = 460 }) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const viewRef = useRef({ scale: 1, tx: 0, ty: 0 });
  const panRef = useRef(null);
  const [hover, setHover] = useState(null);
  const [hidden, setHidden] = useState(() => new Set());
  const [showAll, setShowAll] = useState(false);
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;

  // decode once per payload; these are the only large allocations
  const parsed = useMemo(() => {
    if (!data?.available) return null;
    const x = decode(data.x, Uint16Array);
    const y = decode(data.y, Uint16Array);
    const r = decode(data.r, Uint16Array);
    // points grouped by region index, so a frame sets fillStyle once per region
    const groups = new Map();
    for (let i = 0; i < r.length; i++) {
      if (!r[i]) continue;                       // 0 = no region; never drawn
      let g = groups.get(r[i]);
      if (!g) groups.set(r[i], (g = []));
      g.push(i);
    }
    for (const [k, v] of groups) groups.set(k, Int32Array.from(v));
    return { x, y, r, groups, index: buildIndex(x, y) };
  }, [data]);

  const byIndex = useMemo(() => {
    const m = new Map();
    for (const reg of (data?.regions || [])) m.set(reg.index, reg);
    return m;
  }, [data]);

  function draw() {
    const cv = canvasRef.current, p = parsed;
    if (!cv || !p) return;
    const ctx = cv.getContext("2d");
    const w = cv.clientWidth, h = cv.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== w * dpr || cv.height !== h * dpr) {
      cv.width = w * dpr; cv.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const v = viewRef.current;
    // uint16 -> CSS px. Fit the section's ACTUAL quantised extent: both axes now
    // share one scale, so the short one occupies less than the full range and
    // fitting a square would letterbox it wrongly.
    const [qw, qh] = data.qextent || [65535, 65535];
    const base = Math.min(w / qw, h / qh);
    const s = base * v.scale;
    const size = Math.max(0.6, DOT * Math.sqrt(v.scale));
    // Cells are round, but only once a dot is big enough for the shape to be
    // visible. Below ROUND_ABOVE_PX a circle and a square cover the same one or
    // two pixels, and arcs cost noticeably more than fillRect across 85,000
    // points -- so the cheap path is used exactly where it is indistinguishable.
    const round = size > ROUND_ABOVE_PX;
    const r = size / 2;
    for (const [idx, pts] of p.groups) {
      if (hiddenRef.current.has(idx)) continue;   // toggled off: alpha 0
      const reg = byIndex.get(idx);
      ctx.fillStyle = (palette === "rank" ? reg?.rank : reg?.allen) || "#8aa0b8";
      if (!round) {
        for (let k = 0; k < pts.length; k++) {
          const i = pts[k];
          ctx.fillRect(p.x[i] * s + v.tx, p.y[i] * s + v.ty, size, size);
        }
        continue;
      }
      // ONE path per region, filled once, rather than a path per cell. The
      // moveTo before each arc matters: without it the arcs are joined by a
      // line to the previous subpath and the section fills with spokes.
      ctx.beginPath();
      for (let k = 0; k < pts.length; k++) {
        const i = pts[k];
        const cx = p.x[i] * s + v.tx + r;
        const cy = p.y[i] * s + v.ty + r;
        ctx.moveTo(cx + r, cy);
        ctx.arc(cx, cy, r, 0, TAU);
      }
      ctx.fill();
    }
  }

  // The whole section, centred. Used on first paint, when the payload changes,
  // and by the reset button -- zooming into a boundary is easy to do and
  // tedious to undo by scrolling back out, and there is no other way home.
  function fit() {
    const cv = canvasRef.current;
    if (!cv) return;
    const w = cv.clientWidth, h = cv.clientHeight;
    const [qw, qh] = data.qextent || [65535, 65535];
    const base = Math.min(w / qw, h / qh);
    viewRef.current = { scale: 1, tx: (w - qw * base) / 2,
                        ty: (h - qh * base) / 2 };
    draw();
  }

  useEffect(() => {
    if (!parsed) return undefined;
    fit();
    return undefined;
  }, [parsed, palette]);

  useEffect(() => { draw(); }, [hidden, palette]);
  useEffect(() => {
    const on = () => draw();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, [parsed]);

  // The wheel handler has to be attached NATIVELY with { passive: false }.
  // React routes wheel through a passive root listener, so `preventDefault()` in
  // a JSX onWheel is ignored and the page scrolls behind the zoom. This is the
  // whole reason the handler is not simply `onWheel={...}` on the canvas.
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return undefined;
    const handler = (e) => onWheel(e);
    cv.addEventListener("wheel", handler, { passive: false });
    return () => cv.removeEventListener("wheel", handler);
  }, [parsed, palette]);

  const toLocal = (evt) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return [evt.clientX - rect.left, evt.clientY - rect.top];
  };

  function onMove(evt) {
    const p = parsed;
    if (!p) return;
    const [mx, my] = toLocal(evt);
    if (panRef.current) {
      const v = viewRef.current;
      v.tx = panRef.current.tx0 + (evt.clientX - panRef.current.x0);
      v.ty = panRef.current.ty0 + (evt.clientY - panRef.current.y0);
      draw();
      return;
    }
    const cv = canvasRef.current;
    const [qw, qh] = data.qextent || [65535, 65535];
    const base = Math.min(cv.clientWidth / qw, cv.clientHeight / qh);
    const s = base * viewRef.current.scale;
    const qx = (mx - viewRef.current.tx) / s;
    const qy = (my - viewRef.current.ty) / s;
    // the hover radius is in SCREEN pixels, so it stays the same size to the
    // user as they zoom rather than covering more tissue when zoomed out
    const i = nearest(p.index, qx, qy, HOVER_PX / s,
                      (k) => !hiddenRef.current.has(p.r[k]) && p.r[k] !== 0);
    setHover(i < 0 ? null : { i, reg: byIndex.get(p.r[i]), mx, my });
  }

  function onWheel(evt) {
    evt.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const mx = evt.clientX - rect.left, my = evt.clientY - rect.top;
    const v = viewRef.current;
    const k = Math.exp(-evt.deltaY * 0.0015);
    // zoom about the cursor: the point under it must not move
    v.tx = mx - (mx - v.tx) * k;
    v.ty = my - (my - v.ty) * k;
    v.scale *= k;
    draw();
  }

  const toggle = (idx) => {
    const next = new Set(hidden);
    if (next.has(idx)) next.delete(idx); else next.add(idx);
    setHidden(next);
  };

  if (!data) return <div className="muted">Loading cells …</div>;
  if (!data.available) return <div className="muted">{data.reason}</div>;

  const all = data.regions || [];
  const top = showAll ? all : all.slice(0, PILLS);
  const restCells = all.slice(PILLS).reduce((a, r) => a + r.count, 0);
  return (
    <div>
      <div ref={wrapRef} style={{ position: "relative", height,
                                  background: "#0b0f14", borderRadius: 8,
                                  border: "1px solid #1c2530", overflow: "hidden" }}>
        <canvas ref={canvasRef}
                style={{ width: "100%", height: "100%", display: "block",
                         cursor: panRef.current ? "grabbing" : "crosshair" }}
                onPointerDown={(e) => {
                  panRef.current = { x0: e.clientX, y0: e.clientY,
                                     tx0: viewRef.current.tx, ty0: viewRef.current.ty };
                  setHover(null);
                }}
                onPointerUp={() => { panRef.current = null; }}
                onPointerLeave={() => { panRef.current = null; setHover(null); }}
                onPointerMove={onMove} />
        <button className="badge" onClick={fit}
                title="Back to the whole section"
                style={{ position: "absolute", right: 8, top: 8, cursor: "pointer",
                         font: "inherit", fontSize: 11, background: "#0b0f14cc" }}>
          reset view
        </button>
        {hover?.reg && (
          <div style={{ position: "absolute", left: hover.mx + 12, top: hover.my + 12,
                        pointerEvents: "none", background: "#11161d",
                        border: "1px solid var(--border)", borderRadius: 6,
                        padding: "4px 8px", fontSize: 11, whiteSpace: "nowrap" }}>
            <b style={{ color: palette === "rank" ? hover.reg.rank : hover.reg.allen }}>
              {hover.reg.acronym}
            </b>{" "}
            <span className="muted">{hover.reg.name}</span>
          </div>
        )}
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 5, marginTop: 6 }}>
        {top.map((reg) => {
          const on = !hidden.has(reg.index);
          const col = palette === "rank" ? reg.rank : reg.allen;
          return (
            <button key={reg.index} className="badge" onClick={() => toggle(reg.index)}
                    title={`${on ? "Hide" : "Show"} ${reg.name} (${reg.count.toLocaleString()} cells)`}
                    style={{ cursor: "pointer", font: "inherit", fontSize: 11,
                             borderColor: on ? col : "var(--border)",
                             opacity: on ? 1 : 0.45,
                             textDecoration: on ? "none" : "line-through" }}>
              <span style={{ display: "inline-block", width: 8, height: 8,
                             borderRadius: 2, marginRight: 5, verticalAlign: -1,
                             background: on ? col : "transparent",
                             border: on ? "none" : `1px solid ${col}` }} />
              {reg.acronym}
            </button>
          );
        })}
        {all.length > PILLS && (
          <button className="badge"
                  style={{ cursor: "pointer", font: "inherit", fontSize: 11 }}
                  title={showAll ? "Show only the most abundant regions"
                    : `${all.length - PILLS} more regions, ${restCells.toLocaleString()} cells `
                      + `— all already drawn, this only adds their toggles`}
                  onClick={() => setShowAll((v) => !v)}>
            {showAll ? "fewer regions" : `+${all.length - PILLS} more regions`}
          </button>
        )}
        {hidden.size > 0 && (
          <button className="badge" style={{ cursor: "pointer", font: "inherit", fontSize: 11 }}
                  onClick={() => setHidden(new Set())}>reset hidden</button>
        )}
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Scroll to zoom, drag to pan, hover a cell for its region, click a pill to
        hide it. All {all.length} regions are drawn
        {all.length > PILLS && !showAll
          && `; the ${PILLS} most abundant have pills, the rest are behind “more regions”`}.
        {" "}{data.n.toLocaleString()} cells, {data.unlabelled.toLocaleString()} unlabelled
        {" "}(no region at this level, not drawn).
      </div>
    </div>
  );
}
