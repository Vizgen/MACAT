// A read-only version of the curation overlay, for final confirmation in Results:
// a static background image with the depth-4 Allen regions drawn over it, with
// the same boundary-thickness, fill-opacity and region-toggle controls curation
// has -- and none of its editing.
//
// The curated annotation raster is never saved: the bundle holds the AUTOMATIC
// label plane, and the curated result is that plane plus the confirmed
// transform. So this redraws `/api/boundaries` through the same `place()` the
// curation canvas composes -- global affine, then the local CSRBF -- rather than
// needing a second raster on disk.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { buildPlace, planeExtent, fitView, overlayLegend } from "./overlayPlace";

const FILL_ALPHA = 0.22;

// Two sets of pills sit under the marker panel -- one hides an ATLAS REGION in
// the overlay, the other drops a MARKER DIVISION from the background image --
// and as bare adjacent rows they look like one control that happens to have
// wrapped. Each gets a rule and a heading naming the layer it acts on.
function PillSection({ layer, title, note, children }) {
  return (
    <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
      <div className="muted" style={{ fontSize: 10, letterSpacing: 0.6,
                                      textTransform: "uppercase", marginBottom: 5 }}>
        <b style={{ color: "var(--text)" }}>{layer}</b>
        <span style={{ opacity: 0.7 }}> · {title}</span>
        {note && <span style={{ opacity: 0.55, textTransform: "none",
                                letterSpacing: 0 }}> — {note}</span>}
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 5 }}>{children}</div>
    </div>
  );
}

export { PillSection };

export default function OverlayCanvas({
  bgUrl, boundaries, transform, meta, height = 420,
  hint = null, extraControls = null,
}) {
  const canvasRef = useRef(null);
  const imgRef = useRef(null);
  const viewRef = useRef({ scale: 1, tx: 0, ty: 0 });
  const panRef = useRef(null);
  const drawRef = useRef(() => {});
  const [alpha, setAlpha] = useState(FILL_ALPHA);
  const [lineScale, setLineScale] = useState(1);
  const [hidden, setHidden] = useState(() => new Set());
  const [bgOn, setBgOn] = useState(true);
  const [tick, setTick] = useState(0);        // background image arrived

  // The confirmed transform, composed exactly as the curation canvas composes
  // it. Memoised per transform, so panning never re-solves the CSRBF.
  const place = useMemo(() => buildPlace(transform), [transform]);
  // The BUNDLE's displayed plane, not the bbox of the regions: that is the frame
  // the background image covers, so anything else would slide the image against
  // the overlay.
  const ext = useMemo(() => planeExtent(meta), [meta]);
  const legend = useMemo(() => overlayLegend(boundaries), [boundaries]);

  useEffect(() => {
    if (!bgUrl) { imgRef.current = null; setTick((v) => v + 1); return undefined; }
    const im = new Image();
    im.onload = () => { imgRef.current = im; setTick((v) => v + 1); };
    im.onerror = () => { imgRef.current = null; setTick((v) => v + 1); };
    im.src = bgUrl;
    return () => { im.onload = null; im.onerror = null; };
  }, [bgUrl]);

  drawRef.current = function draw() {
    const cv = canvasRef.current;
    if (!cv || !ext || !boundaries) return;
    const ctx = cv.getContext("2d");
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const v = viewRef.current;
    const toScreen = (lr, si) => [v.tx + lr * v.scale, v.ty + si * v.scale];

    // the STATIC layer: drawn in plane microns, untouched by the region controls
    const im = imgRef.current;
    if (bgOn && im && im.complete && im.naturalWidth) {
      const [x0, y0] = toScreen(ext.x0, ext.y0);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(im, x0, y0, (ext.x1 - ext.x0) * v.scale,
                    (ext.y1 - ext.y0) * v.scale);
    }

    ctx.lineJoin = "round";
    // fills first, then every outline, so no region's fill covers a neighbour's
    // boundary -- the boundary is the thing being confirmed here
    for (const pass of ["fill", "stroke"]) {
      if (pass === "fill" && alpha <= 0) continue;
      if (pass === "stroke" && lineScale <= 0) continue;
      for (const f of boundaries.features) {
        const off = f.kind === "region" && hidden.has(f.acronym);
        if (off) continue;                       // toggled off: fill AND outline
        if (pass === "fill" && f.geometry !== "polygon") continue;
        const rings = f.rings || [{ points: f.points || [] }];
        ctx.beginPath();
        for (const ring of rings) {
          const pts = ring.points || ring;
          if (!pts || pts.length < 2) continue;
          for (let i = 0; i < pts.length; i++) {
            const [sx, sy] = toScreen(...place(pts[i]));
            if (i) ctx.lineTo(sx, sy); else ctx.moveTo(sx, sy);
          }
          if (f.geometry === "polygon" || f.kind === "outer") ctx.closePath();
        }
        if (pass === "fill") {
          ctx.globalAlpha = alpha;
          ctx.fillStyle = f.color || "#8aa0b8";
          ctx.fill("evenodd");
        } else {
          ctx.globalAlpha = f.kind === "outer" ? 0.95 : 0.85;
          ctx.strokeStyle = f.kind === "outer" ? "#f5fbff" : (f.color || "#9fe3ff");
          ctx.lineWidth = (f.kind === "outer" ? 1.8 : 1.1) * lineScale;
          ctx.setLineDash(f.kind === "midline" ? [6, 5] : []);
          ctx.stroke();
          ctx.setLineDash([]);
        }
        ctx.globalAlpha = 1;
      }
    }
  };

  const fit = () => {
    const cv = canvasRef.current;
    if (!cv || !ext) return;
    const v = fitView(ext, cv.clientWidth, cv.clientHeight);
    if (!v) return;
    viewRef.current = v;
    drawRef.current();
  };

  // Fit only when the SECTION changes. Notably not when `bgUrl` does: a marker
  // division toggle swaps the background image, and throwing away the reader's
  // zoom for that would make the toggles unusable at any magnification.
  useEffect(() => { fit(); }, [ext, boundaries]);
  useEffect(() => { drawRef.current(); },
            [alpha, lineScale, hidden, bgOn, tick, place]);
  useEffect(() => {
    const on = () => fit();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, [meta, boundaries]);

  // native and non-passive: React routes wheel through a passive listener, so a
  // preventDefault in a JSX onWheel is ignored and the page scrolls behind the zoom
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return undefined;
    const handler = (e) => {
      e.preventDefault();
      const rect = cv.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const v = viewRef.current;
      const k = Math.exp(-e.deltaY * 0.0015);
      v.tx = mx - (mx - v.tx) * k;
      v.ty = my - (my - v.ty) * k;
      v.scale *= k;
      drawRef.current();
    };
    cv.addEventListener("wheel", handler, { passive: false });
    return () => cv.removeEventListener("wheel", handler);
  }, []);

  const toggle = (acro) => {
    const next = new Set(hidden);
    if (next.has(acro)) next.delete(acro); else next.add(acro);
    setHidden(next);
  };

  if (!boundaries || !meta) return <div className="muted">Loading overlay …</div>;

  return (
    <div>
      <div style={{ position: "relative", height, background: "#05080c",
                    borderRadius: 8, border: "1px solid #1c2530", overflow: "hidden" }}>
        <canvas ref={canvasRef}
                style={{ width: "100%", height: "100%", display: "block",
                         cursor: panRef.current ? "grabbing" : "grab",
                         touchAction: "none" }}
                onPointerDown={(e) => {
                  e.currentTarget.setPointerCapture(e.pointerId);
                  panRef.current = { x0: e.clientX, y0: e.clientY,
                                     tx0: viewRef.current.tx, ty0: viewRef.current.ty };
                }}
                onPointerUp={() => { panRef.current = null; }}
                onPointerCancel={() => { panRef.current = null; }}
                onPointerMove={(e) => {
                  const p = panRef.current;
                  if (!p) return;
                  const v = viewRef.current;
                  v.tx = p.tx0 + (e.clientX - p.x0);
                  v.ty = p.ty0 + (e.clientY - p.y0);
                  drawRef.current();
                }} />
        <button className="badge" onClick={fit}
                title="Back to the whole section"
                style={{ position: "absolute", right: 8, top: 8, cursor: "pointer",
                         font: "inherit", fontSize: 11, background: "#0b0f14cc" }}>
          reset view
        </button>
      </div>
      {hint && <div className="muted" style={{ fontSize: 11, marginTop: 5 }}>{hint}</div>}
      <div className="row" style={{ gap: 14, marginTop: 8, alignItems: "flex-end" }}>
        <label style={{ flex: "1 1 120px" }}>
          <div className="muted" style={{ fontSize: 11 }}>
            boundary {lineScale.toFixed(1)}×
          </div>
          <input type="range" min={0} max={3} step={0.1} value={lineScale}
                 onChange={(e) => setLineScale(+e.target.value)}
                 style={{ width: "100%" }} />
        </label>
        <label style={{ flex: "1 1 120px" }}>
          <div className="muted" style={{ fontSize: 11 }}>
            fill {Math.round(alpha * 100)}%
          </div>
          <input type="range" min={0} max={1} step={0.02} value={alpha}
                 onChange={(e) => setAlpha(+e.target.value)}
                 style={{ width: "100%" }} />
        </label>
        <button className="badge" onClick={() => setBgOn((b) => !b)}
                title="Hide the background image and look at the regions alone"
                style={{ cursor: "pointer", font: "inherit", fontSize: 11,
                         opacity: bgOn ? 1 : 0.5 }}>
          background {bgOn ? "on" : "off"}
        </button>
      </div>
      {/* Overlay BEFORE background, in both panels. Two reasons: it puts the
          same first section under the DAPI panel (which has no background
          pills) and the marker panel, so the two read as one layout; and it
          stacks the controls the way the drawing does, overlay on top of
          background. */}
      <PillSection layer="Overlay" title="depth-4 regions"
                   note="hides the region from the drawing above">
        {legend.map((r) => {
          const on = !hidden.has(r.acronym);
          return (
            <button key={r.acronym} className="badge" onClick={() => toggle(r.acronym)}
                    title={`${on ? "Hide" : "Show"} ${r.name}`}
                    style={{ cursor: "pointer", font: "inherit", fontSize: 11,
                             borderColor: on ? r.color : "var(--border)",
                             opacity: on ? 1 : 0.45,
                             textDecoration: on ? "none" : "line-through" }}>
              <span style={{ display: "inline-block", width: 9, height: 9,
                             borderRadius: 2, marginRight: 6, verticalAlign: -1,
                             background: on ? r.color : "transparent",
                             border: on ? "none" : `1px solid ${r.color}` }} />
              {r.acronym}
            </button>
          );
        })}
        {hidden.size > 0 && (
          <button className="badge" onClick={() => setHidden(new Set())}
                  style={{ cursor: "pointer", font: "inherit", fontSize: 11 }}>
            show all
          </button>
        )}
      </PillSection>
      {extraControls}
    </div>
  );
}
