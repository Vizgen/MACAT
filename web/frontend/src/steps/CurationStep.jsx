import React, { useEffect, useRef, useState } from "react";
import {
  affineMatrix, applyAffine, IDENTITY_PARAMS, isIdentity,
  anchoredScaleFromHandle, rotationFromHandle,
} from "../affine";
import {
  GRID_ALGO, GRID_ROWS, GRID_COLS, GRID_PRESETS, maxDisp,
  MAX_GRID_DIM, gridNodes, gridRadius,
  nodeKinds, movableCount,
  NODE_CORNER, NODE_EDGE_V, NODE_EDGE_H, NODE_FREE,
  buildWarp, warpWeights, warpPoints, validateGrid,
  chainPlacer, composedJacMin, MAX_LAYERS, COMPOSED_JAC_MIN,
} from "../warp";
import { TOOLS, getTool, DEFAULT_TOOL, initialToolState } from "../curation/tools";
import { overhangSummary } from "../jobapi";
import { curationConfirmable } from "../jobActions";
import { rankRecolor } from "../regionColors";
import HelpTip from "../HelpTip";

// Relative URLs -> Vite proxies /api to the backend in dev; same-origin when the
// built app is served by FastAPI.
const API = "";

async function jget(url) {
  const r = await fetch(API + url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(API + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

const HANDLE_PX = 6;      // handle dot radius (screen px)
const HIT_PX = 11;        // handle grab tolerance (screen px)
const ROT_OFFSET_PX = 30; // rotation handle distance above the top edge (screen px)
// The lattice is an editing aid, not the primary anatomical overlay. Keep its visible
// markers compact while HIT_PX provides a comfortably larger invisible grab target.
const NODE_PX = 3.3;      // draggable node dot radius (screen px)
const EDGE_PX = 2.3;      // fixed-corner marker half-size (screen px)
const GRID_LW = 0.9;      // normal grid line width (screen px)
const GRID_SEG = 8;       // samples per lattice cell edge when drawing the grid
const LANDMARK_PX = 5.5;  // paired-point marker radius (screen px)
const IDENTITY_STAT = {
  ok: true, reason: "identity", nMoved: 0, maxDisp: 0, minDet: 1, maxDet: 1,
};

function pointInRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if (((yi > y) !== (yj > y))
      && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-12) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

// point-in-(convex)-quadrilateral, corners in order; used to grab the box body
function pointInQuad(p, q) {
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const a = q[i], b = q[(i + 1) % 4];
    const cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
    if (cross !== 0) {
      const s = cross > 0 ? 1 : -1;
      if (sign === 0) sign = s;
      else if (s !== sign) return false;
    }
  }
  return true;
}

// map a persisted backend affine {tx,ty,rot_deg,sx,sy} to UI params; the centre
// (cx,cy) is derived from the bundle bbox so only the transform fields matter.
function affineToParams(a) {
  if (!a) return { ...IDENTITY_PARAMS };
  return {
    tx: a.tx ?? 0, ty: a.ty ?? 0,
    rotDeg: a.rot_deg ?? 0,
    sx: a.sx ?? 1, sy: a.sy ?? 1,
  };
}

// equality within tiny epsilons so a re-loaded baseline never reads as dirty
function paramsEqual(a, b) {
  return Math.abs(a.tx - b.tx) < 1e-6 && Math.abs(a.ty - b.ty) < 1e-6 &&
    Math.abs(a.rotDeg - b.rotDeg) < 1e-6 &&
    Math.abs(a.sx - b.sx) < 1e-9 && Math.abs(a.sy - b.sy) < 1e-9;
}

// how many lattice nodes differ between two node lists (same length assumed)
function nMovedOf(a, b) {
  let n = 0;
  for (let i = 0; i < a.length; i++)
    if (Math.abs(b[i][0] - a[i][0]) > 1e-6 || Math.abs(b[i][1] - a[i][1]) > 1e-6) n++;
  return n;
}

const copyNodes = (nodes) => nodes.map((p) => [p[0], p[1]]);

// Build the editing lattice for a job: the job's OWN grid if it already has a
// confirmed warp, else the current default (13x13).
//
// A warp saved at 5x5 is restored AT 5x5 -- its 9 displacements are never resampled
// onto a 13x13 lattice, because there is no correct way to do that silently (the
// dst values are node positions, not a field we may reinterpolate; guessing would
// change where the labels land without telling anyone). The grid a job was curated
// with therefore travels with the job. Only the extent still has to agree: after a
// re-registration at a different AP the stored node positions are meaningless, so we
// fall back to a default identity grid rather than bend the wrong place.
// A saved curation may be a stack. The LAST layer becomes the live, editable
// grid and the earlier ones come back baked -- so a job that never used layering
// reopens exactly as it always did, with its lattice draggable, while a layered
// one reopens with its most recent layer still in hand. "Remove last layer"
// is how you get back into an earlier one.
function splitLayers(warp) {
  if (!warp) return { baked: [], live: null };
  const layers = Array.isArray(warp.layers) && warp.layers.length
    ? warp.layers : [warp];
  const usable = layers.filter(
    (ly) => ly && Array.isArray(ly.src) && Array.isArray(ly.dst));
  if (!usable.length) return { baked: [], live: null };
  return { baked: usable.slice(0, -1), live: usable[usable.length - 1] };
}

function buildGrid(warp, extent) {
  const dflt = { rows: GRID_ROWS, cols: GRID_COLS };
  const stored = (warp && Array.isArray(warp.src) && Array.isArray(warp.dst) &&
                  Number(warp.rows) > 2 && Number(warp.cols) > 2 &&
                  Number(warp.rows) <= MAX_GRID_DIM && Number(warp.cols) <= MAX_GRID_DIM)
    ? { rows: Number(warp.rows), cols: Number(warp.cols) } : null;
  const dims = stored || dflt;
  const src = gridNodes(extent, dims.rows, dims.cols);
  const R = gridRadius(extent, dims.rows, dims.cols);
  const base = { ...dims, src, R, extent, dst: copyNodes(src), restored: false,
                 dispFrac: null };
  if (!stored) return base;
  if (warp.src.length !== src.length || warp.dst.length !== src.length) return base;
  for (let i = 0; i < src.length; i++)
    if (Math.abs(Number(warp.src[i][0]) - src[i][0]) > 1.0 ||
        Math.abs(Number(warp.src[i][1]) - src[i][1]) > 1.0) return base;
  // Restore the budget and the radius the warp was CONFIRMED with, not the
  // current defaults. Both are honoured by the backend (finalize_composite reads
  // warp["R"] and warp["disp_frac"]), so recomputing them here would let the
  // preview and the finalized labels disagree -- and for disp_frac it would do
  // worse than that: a warp authored by the elastic tool would come back looking
  // invalid, because the default cap is a third of the budget it was accepted at.
  const storedFrac = Number(warp.disp_frac);
  return { ...base,
           R: Number(warp.R) > 0 ? Number(warp.R) : R,
           dispFrac: Number.isFinite(storedFrac) && storedFrac > 0 ? storedFrac : null,
           dst: warp.dst.map((p) => [Number(p[0]), Number(p[1])]),
           restored: true };
}

export default function CurationStep({ job, onComplete, onDirty }) {
  const reg = job?.register?.result || {};
  const overhang = overhangSummary(reg.atlas_overhang);
  const bundlePath = reg.bundle_path || "";
  const pq = "?path=" + encodeURIComponent(bundlePath);
  const [meta, setMeta] = useState(null);
  // authoritative global-affine parameters (plane microns [lr, si]); scale about
  // the fixed box centre. This is the single source of truth sent to the backend.
  // When re-entering curation after a confirm, seed from the persisted affine so
  // the box, badges and preview reflect the last accepted alignment (not identity).
  const initParams = affineToParams(job?.manual?.affine);
  const [params, setParams] = useState(initParams);
  const [view, setView] = useState("after"); // 'before' = auto align, 'after' = manual
  const [mode, setMode] = useState("global"); // 'global' = affine box, 'local' = warp grid
  const [localTool, setLocalTool] = useState(DEFAULT_TOOL);
  // Every tool's own state lives here, keyed by tool id and initialised by the
  // tool's initState(). Deliberately generic: the host must not know what any
  // tool keeps, or deleting a tool would leave dead state behind.
  const [toolState, setToolState] = useState(initialToolState);
  const toolStateRef = useRef(toolState);   // pointer handlers run outside render
  const localToolRef = useRef(localTool);  // ditto for which tool is active
  const [regionView, setRegionView] = useState("alignment");
  // Regions switched off in the legend, per view: Landmarks and each depth have
  // different region sets, so one shared set would hide an unrelated structure
  // when you changed depth. Shape: { alignment: Set, depth2: Set, ... }.
  const [regionHidden, setRegionHidden] = useState({});
  const regionHiddenRef = useRef(regionHidden);   // draw() runs outside render
  const [regionLoading, setRegionLoading] = useState(null);
  // background under the atlas overlay. DAPI is the default: it shows the full
  // tissue outline and cortical layering, which is what the alignment is judged on;
  // density is kept as a toggle for confirming where the cells actually are.
  const [bg, setBg] = useState("dapi");
  const [bgLoading, setBgLoading] = useState(false);
  // Display-only controls. They never enter the affine/warp payload or exports.
  const [regionLineScale, setRegionLineScale] = useState(1);
  const [regionFillAlpha, setRegionFillAlpha] = useState(0.25);
  const [histLen, setHistLen] = useState(0);
  const [legend, setLegend] = useState([]); // distinct regions at the selected hierarchy level
  const [alignmentLegend, setAlignmentLegend] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  // live validity/stats of the local warp grid (mirrors pipeline_api.validate_warp_grid)
  const [gridStat, setGridStat] = useState(IDENTITY_STAT);
  const [warpMsg, setWarpMsg] = useState(null); // transient "drag rolled back" notice
  // the grid this job is being curated with: the default for a new curation, or the
  // dimensions carried by an already-confirmed warp (see buildGrid)
  const [dims, setDims] = useState({ rows: GRID_ROWS, cols: GRID_COLS, restored: false });

  const canvasRef = useRef(null);
  const featsRef = useRef([]);   // read-only selected boundary features (official colours)
  const hierarchyFeatsRef = useRef({});
  const hierarchyLabelsRef = useRef({});
  const regionChoiceRef = useRef("alignment");
  const alignmentFeatsRef = useRef([]);
  const labelsRef = useRef([]);  // interior acronym anchors for the selected level
  const viewRef = useRef({ scale: 1, tx: 0, ty: 0, fitted: false });
  const imgRef = useRef(null);   // cell-density Image
  const dapiRef = useRef(null);  // plane-aligned DAPI Image (same extent as density)
  const transcriptRef = useRef(null); // lazy all-gene transcript-density Image
  const markerRef = useRef(null);     // lazy anatomical marker-gene composite
  const markerOnRef = useRef(null);   // live mirror of markerOn for the fetch
  const [markerLegend, setMarkerLegend] = useState([]);
  // divisions currently switched on. null = "all of them", which is also what
  // an empty `regions` param means to the server, so first load needs no list.
  const [markerOn, setMarkerOn] = useState(null);
  const bgChoiceRef = useRef("dapi"); // prevents a late image load overriding a newer choice
  const paramsRef = useRef(params); // live params (mutated during a drag)
  const baselineRef = useRef(initParams); // params on entry; dirty = diverged from this
  const dragRef = useRef(null);  // active handle drag {type, start...}
  const panRef = useRef(null);   // {x0,y0,tx0,ty0}
  const handlesRef = useRef([]); // screen-space handle hit targets
  const regionHitsRef = useRef([]); // transformed closed polygons for hover
  const hoverRegionRef = useRef(null);
  const histRef = useRef([]);    // undo stack of {params, dst} snapshots
  // Baked warp layers, applied in order BEFORE the live grid. Each is a lattice
  // the user finished with and committed so they could keep dragging on a fresh
  // identity grid; the composition is what reaches the backend. Undo deliberately
  // does not reach across a boundary -- "Remove last layer" does that, so a
  // reflexive ctrl-Z cannot silently discard a whole round of work.
  const layersRef = useRef([]);
  const [nLayers, setNLayers] = useState(0);
  const rafRef = useRef(0);
  const drawRef = useRef(() => {});
  // local warp grid. `src` (the lattice) and `R` are FIXED for a bundle, so the
  // CSRBF kernel is LU-factored once in `fac` and only the right-hand side is
  // re-solved while a node is dragged. `dst` is the live, user-edited lattice.
  const gridRef = useRef(null);   // {src, dst, R, extent, fac}
  const baseDstRef = useRef(null); // dst on entry; grid dirty = diverged from this
  const baselineDimsRef = useRef(null); // saved/default dimensions on entry
  const nodesRef = useRef([]);     // screen-space node hit targets [{idx, pt, fixed}]
  const statRef = useRef(IDENTITY_STAT); // same value as gridStat, readable from draw()
  const pointerMoveRef = useRef(null);
  const pointerUpRef = useRef(null);

  // keep the drawable copy and the rendered copy of the grid validity in sync
  const applyStat = (v) => { statRef.current = v; setGridStat(v); };
  const revalidate = () => {
    const g = gridRef.current;
    if (!g) return IDENTITY_STAT;
    const v = validateGrid(g.src, g.dst, g.R, g.extent, g.rows, g.cols, g.dispFrac);
    applyStat(v);
    return v;
  };

  // ---------- geometry helpers (all plane microns [lr, si]) ----------
  const geom = () => {
    const m = meta;
    return {
      cx: (m.lr0 + m.lr1) / 2, cy: (m.si0 + m.si1) / 2,
      halfW: (m.lr1 - m.lr0) / 2, halfH: (m.si1 - m.si0) / 2,
    };
  };
  const matrix = () => {
    const g = geom(); const p = paramsRef.current;
    return affineMatrix(g.cx, g.cy, p.tx, p.ty, p.rotDeg, p.sx, p.sy);
  };

  // ---------- coordinate transforms (plane microns <-> screen CSS px) ----------
  const toScreen = (lr, si) => {
    const v = viewRef.current;
    return [v.tx + lr * v.scale, v.ty + si * v.scale];
  };
  const toUm = (sx, sy) => {
    const v = viewRef.current;
    return [(sx - v.tx) / v.scale, (sy - v.ty) / v.scale];
  };
  const evtUm = (evt) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return toUm(evt.clientX - rect.left, evt.clientY - rect.top);
  };

  // transform-box corners / edge-mids / rotation handle, in screen px
  const computeHandles = () => {
    if (!meta) return { corners: [], list: [] };
    const g = geom(); const M = matrix();
    const P = (lr, si) => toScreen(...applyAffine(M, [lr, si]));
    const corners = [
      P(meta.lr0, meta.si0), P(meta.lr1, meta.si0),
      P(meta.lr1, meta.si1), P(meta.lr0, meta.si1),
    ];
    const topMid = P(g.cx, meta.si0);
    const list = [
      { type: "corner", pt: corners[0], signX: -1, signY: -1 },
      { type: "corner", pt: corners[1], signX: 1, signY: -1 },
      { type: "corner", pt: corners[2], signX: 1, signY: 1 },
      { type: "corner", pt: corners[3], signX: -1, signY: 1 },
      { type: "x", pt: P(meta.lr1, g.cy), signX: 1, signY: 0 },
      { type: "x", pt: P(meta.lr0, g.cy), signX: -1, signY: 0 },
      { type: "y", pt: topMid, signX: 0, signY: -1 },
      { type: "y", pt: P(g.cx, meta.si1), signX: 0, signY: 1 },
    ];
    // rotation handle: fixed screen distance above the (transformed) top edge
    const ctS = toScreen(...applyAffine(M, [g.cx, g.cy]));
    let up = [topMid[0] - ctS[0], topMid[1] - ctS[1]];
    const n = Math.hypot(up[0], up[1]) || 1;
    up = [up[0] / n, up[1] / n];
    const rot = [topMid[0] + up[0] * ROT_OFFSET_PX, topMid[1] + up[1] * ROT_OFFSET_PX];
    list.push({ type: "rot", pt: rot, anchor: topMid });
    return { corners, list };
  };

  // ---------- draw everything ----------
  const draw = () => {
    const cv = canvasRef.current;
    if (!cv || !meta) return;
    const ctx = cv.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const cw = cv.clientWidth, ch = cv.clientHeight;
    if (cv.width !== cw * dpr || cv.height !== ch * dpr) {
      cv.width = cw * dpr; cv.height = ch * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);

    // background (the tissue; stays fixed — the atlas moves over it). DAPI and
    // density are resampled onto the same plane grid and drawn over the same
    // extent, so switching between them cannot shift the image under the overlay.
    const dapiOk = dapiRef.current && dapiRef.current.complete
                   && dapiRef.current.naturalWidth;
    // For a DAPI-capable job, never substitute density while DAPI is loading:
    // that caused a visible density flash on entry. The canvas stays neutral for
    // the brief preload, then paints DAPI. Density appears only when explicitly
    // selected or when the backend says this job has no DAPI.
    const lazyRef = bg === "transcripts" ? transcriptRef
                  : bg === "markers" ? markerRef : null;
    const lazyOk = lazyRef?.current?.complete && lazyRef.current.naturalWidth;
    const img = bg === "dapi"
      ? (dapiOk ? dapiRef.current : (meta?.has_dapi ? null : imgRef.current))
      : (lazyRef ? (lazyOk ? lazyRef.current : null) : imgRef.current);
    if (img && img.complete && img.naturalWidth) {
      const [x0, y0] = toScreen(meta.lr0, meta.si0);
      const v = viewRef.current;
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(img, x0, y0, (meta.lr1 - meta.lr0) * v.scale, (meta.si1 - meta.si0) * v.scale);
    }

    const after = view === "after";
    const local = after && mode === "local";
    const M = matrix();
    const g = gridRef.current;
    const nMoved = g ? nMovedOf(g.src, g.dst) : 0;
    // Point mapper, atlas plane microns -> displayed microns: the global affine
    // THEN the local forward CSRBF (centers = src, solving F(src_i) = dst_i). This
    // is exactly the composition pipeline_api.warp_composite inverts when it
    // resamples the leaf-level label raster, so what is drawn is where the labels
    // will land.
    let place;
    let warpOnly = (p) => p;
    // Baked layers sit BETWEEN the affine and the live grid, in that order, which
    // is exactly the order finalize inverts them in. The live grid's own lattice
    // is regular in the ALREADY-baked space, so it needs no mapping of its own.
    const baked = chainPlacer(layersRef.current);
    if (!after) {
      place = (p) => p;
    } else if (g && nMoved > 0) {
      const w = warpWeights(g.fac, g.dst);
      warpOnly = (p) => warpPoints(g.fac, w, [p])[0];
      place = (p) => warpOnly(baked(applyAffine(M, p)));
    } else {
      place = (p) => baked(applyAffine(M, p));
    }

    // Fill the same closed polygons used for the outline in every edit mode.
    // Opacity is display-only and can be set to zero when an outline-only view is
    // preferred. Canvas' even-odd rule preserves interior holes.
    if (regionFillAlpha > 0) {
      for (const feature of featsRef.current) {
        if (feature.kind === "outer") continue;
        if (feature.geometry !== "polygon" || !feature.rings?.length) continue;
        // A legend toggle hides the FILL and keeps the outline. The outline is
        // the thing being aligned, so it should stay put; the fill is the
        // colour wash that hides the DAPI underneath it, which is what you
        // actually want out of the way when checking one structure.
        if (isRegionHidden(regionKey(feature))) continue;
        ctx.beginPath();
        for (const ring of feature.rings) {
          const screen = ring.points.map((pt) => toScreen(...place(pt)));
          if (screen.length < 4) continue;
          screen[screen.length - 1] = [...screen[0]];
          screen.forEach(([sx, sy], i) => (
            i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy)
          ));
          ctx.closePath();
        }
        ctx.fillStyle = feature.color || "#8aa0b8";
        ctx.globalAlpha = regionFillAlpha;
        ctx.fill("evenodd");
      }
      ctx.globalAlpha = 1;
    }

    // In local mode the grid is deliberately painted below the anatomical overlay:
    // region boundaries and labels are the alignment target; the grid is only an
    // interaction reference.
    // the lattice is drawn by the host because it owns the node hit-targets
    if (local && activeTool().showsGrid) drawGrid(ctx); else nodesRef.current = [];

    // Read-only hierarchy boundaries. Draw a dark casing followed by a bright stroke
    // so the anatomy stays legible over both bright DAPI and black background.
    regionHitsRef.current = [];
    ctx.lineJoin = "round";
    for (const f of featsRef.current) {
      const geometries = f.rings || [{ points: f.points || [] }];
      const polygonScreens = [];
      const isRegion = f.kind === "region" || f.kind === "alignment_region";
      const hovered = isRegion && hoverRegionRef.current === f.acronym;
      const alignment = f.kind === "alignment_region";
      let color;
      let width;
      let alpha = 1;
      let casing = true;
      if (f.kind === "outer") {
        color = "#f5fbff"; width = 3.4; ctx.setLineDash([]);
      } else if (f.kind === "midline") {
        color = "#d8f3ff"; width = 2.7; ctx.setLineDash([6, 5]);
      } else if (hovered) {
        color = "#ffffff"; width = 3.2; ctx.setLineDash([]);
      } else if (alignment) {
        color = f.optional ? "#8fc9e3" : "#bde9ff";
        width = f.optional ? 1.8 : 2.5;
        alpha = f.optional ? 0.72 : 0.95;
        ctx.setLineDash([]);
      } else if (regionView === "alignment") {
        color = "#79b7d2"; width = 0.9; alpha = 0.2; casing = false;
        ctx.setLineDash([]);
      } else {
        color = "#a8dcf3"; width = 1.35; alpha = 0.72; casing = false;
        ctx.setLineDash([]);
      }
      // The control affects every atlas line the user sees, including the outer
      // tissue outline and midline. Limiting it to inner regions made the slider
      // appear broken because the brightest reference lines did not change.
      width *= regionLineScale;
      // At 0 the strokes are SKIPPED rather than drawn thin, for two reasons.
      // `ctx.lineWidth = 0` does not hide a line: the canvas spec ignores zero
      // and negative values and leaves the previous width in place, so the line
      // would come out at whatever the last feature happened to use. And the
      // casing below is drawn at `width + 2.4`, so a dark outline would survive
      // even if the bright stroke did vanish. The loop itself still runs: it is
      // what collects `polygonScreens` for the fills and pushes the hover
      // hit-targets, neither of which should depend on line thickness.
      const strokes = width > 0;
      for (const geometry of geometries) {
        const pts = geometry.points;
        if (!pts || pts.length < 2) continue;
        const screen = pts.map((pt) => toScreen(...place(pt)));
        // Polygon rings arrive closed from the API. Reuse the transformed first
        // vertex exactly so floating-point affine/CSRBF evaluation cannot leave a
        // visible seam at the final segment.
        if (f.geometry === "polygon" && screen.length > 2) {
          screen[screen.length - 1] = [...screen[0]];
          polygonScreens.push({ role: geometry.role, points: screen });
        }
        ctx.beginPath();
        for (let i = 0; i < screen.length; i++) {
          const [sx, sy] = screen[i];
          i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
        }
        if (f.geometry === "polygon") ctx.closePath();
        if (casing && strokes) {
          ctx.strokeStyle = "rgba(2,6,12,0.9)";
          ctx.lineWidth = width + 2.4;
          ctx.globalAlpha = 0.92;
          ctx.stroke();
        }
        if (strokes) {
          ctx.strokeStyle = color;
          ctx.lineWidth = width;
          ctx.globalAlpha = alpha;
          ctx.stroke();
        }
      }
      if (f.geometry === "polygon" && polygonScreens.length) {
        regionHitsRef.current.push({
          acronym: f.acronym, name: f.name, members: f.members || [],
          component: f.component, rings: polygonScreens,
          // atlas-micron rings, kept by reference: the elastic push needs the
          // region's MATERIAL footprint (affine only, no warp), not the screen
          // polygon, because the lattice lives in the pre-warp frame.
          dataRings: f.rings,
        });
      }
    }
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    // Interior hierarchy acronym labels (official colour), carried by the affine.
    const labels = labelsRef.current;
    if (labels && labels.length) {
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.font = "600 12px system-ui, sans-serif";
      for (const L of labels) {
        const showLabel = regionView !== "alignment"
          || hoverRegionRef.current === L.acronym;
        // the acronym goes with the fill it names
        if (!showLabel || isRegionHidden(L.acronym)) continue;
        const [sx, sy] = toScreen(...place([L.lr, L.si]));
        if (sx < -60 || sy < -20 || sx > cw + 60 || sy > ch + 20) continue;
        ctx.lineJoin = "round"; ctx.lineWidth = 4;
        ctx.strokeStyle = "rgba(2,6,12,0.92)";
        ctx.strokeText(L.short, sx, sy);
        ctx.fillStyle = L.color || "#e7f7ff";
        ctx.fillText(L.short, sx, sy);
      }
      ctx.textAlign = "start";
    }

    // Whatever the active tool wants to draw on top of the atlas overlay.
    // `warpOnly` is handed in because it is frame-local: a tool drawing a
    // shape in lattice space needs the SAME mapper the boundaries used.
    if (local) activeTool().draw?.(editor(localTool, { warpOnly }), ctx);

    // transform box + handles: GLOBAL mode only. In local mode the box handles are
    // hidden (and un-hittable) so the two edit modes can never fight over a drag.
    if (after && !local) {
      const { corners, list } = computeHandles();
      handlesRef.current = list;
      ctx.strokeStyle = "#ffcf5c"; ctx.lineWidth = 1.4; ctx.globalAlpha = 0.95;
      ctx.beginPath();
      corners.forEach((c, i) => (i ? ctx.lineTo(c[0], c[1]) : ctx.moveTo(c[0], c[1])));
      ctx.closePath(); ctx.stroke();
      // rotation stem
      const rotH = list.find((h) => h.type === "rot");
      if (rotH) {
        ctx.beginPath();
        ctx.moveTo(rotH.anchor[0], rotH.anchor[1]);
        ctx.lineTo(rotH.pt[0], rotH.pt[1]);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      for (const h of list) {
        ctx.beginPath();
        ctx.arc(h.pt[0], h.pt[1], HANDLE_PX, 0, 7);
        ctx.fillStyle = h.type === "rot" ? "#7ee0d6" : "#ffcf5c";
        ctx.fill();
        ctx.strokeStyle = "#0d1117"; ctx.lineWidth = 1.5; ctx.stroke();
      }
    } else {
      handlesRef.current = [];
    }

  };

  // ---------- local warp grid overlay ----------
  // Draws the WARPED images of the straight src lattice lines (sampled per cell
  // edge), so the curves show the actual deformation field rather than straight
  // chords between nodes, plus one marker per node: a filled dot for each
  // draggable interior/edge nodes, with hollow squares only for the 4 fixed corners.
  // Everything here already lives in DISPLAYED microns (the grid is applied after
  // the affine), so it only goes through toScreen.
  const drawGrid = (ctx) => {
    const g = gridRef.current;
    if (!g) { nodesRef.current = []; return; }
    const w = warpWeights(g.fac, g.dst);
    const F = (pts) => warpPoints(g.fac, w, pts);
    const bad = !statRef.current.ok;
    const { rows, cols } = g;
    const at = (r, c) => g.src[r * cols + c];

    const lines = [];
    for (let r = 0; r < rows; r++) {
      const line = [];
      for (let c = 0; c < cols - 1; c++) {
        const a = at(r, c), b = at(r, c + 1);
        for (let k = 0; k < GRID_SEG; k++) {
          const t = k / GRID_SEG;
          line.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
        }
      }
      line.push(at(r, cols - 1));
      lines.push(line);
    }
    for (let c = 0; c < cols; c++) {
      const line = [];
      for (let r = 0; r < rows - 1; r++) {
        const a = at(r, c), b = at(r + 1, c);
        for (let k = 0; k < GRID_SEG; k++) {
          const t = k / GRID_SEG;
          line.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
        }
      }
      line.push(at(rows - 1, c));
      lines.push(line);
    }

    ctx.save();
    ctx.setLineDash([]);
    // A valid grid stays quiet and translucent. Invalid geometry alone earns the
    // high-contrast, heavier red treatment.
    ctx.lineWidth = bad ? 2.4 : GRID_LW;
    ctx.strokeStyle = bad ? "rgba(255,72,72,0.98)" : "rgba(0,229,255,0.26)";
    for (const line of lines) {
      const pts = F(line);
      ctx.beginPath();
      for (let i = 0; i < pts.length; i++) {
        const [sx, sy] = toScreen(pts[i][0], pts[i][1]);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      }
      ctx.stroke();
    }

    const kinds = nodeKinds(rows, cols);
    const list = [];
    for (let i = 0; i < g.dst.length; i++) {
      const [sx, sy] = toScreen(g.dst[i][0], g.dst[i][1]);
      const kind = kinds[i];
      list.push({ idx: i, pt: [sx, sy], kind });
      ctx.beginPath();
      if (kind === NODE_CORNER) {
        // fixed: hollow grey square, deliberately recessive
        ctx.rect(sx - EDGE_PX, sy - EDGE_PX, EDGE_PX * 2, EDGE_PX * 2);
        ctx.strokeStyle = "rgba(190,200,210,0.75)"; ctx.lineWidth = 1.2;
        ctx.stroke();
      } else {
        // draggable. Interior = solid yellow (free 2D); constrained edge = solid
        // cyan, matching the lattice colour so it reads as "part of the boundary".
        // Dark halo on both so they survive bright and dark DAPI alike.
        ctx.arc(sx, sy, NODE_PX, 0, 7);
        ctx.fillStyle = bad ? "#f85149"
          : (kind === NODE_FREE ? "#ffd23f" : "#40e0ff");
        ctx.fill();
        ctx.strokeStyle = "rgba(6,10,16,0.95)"; ctx.lineWidth = 1.3;
        ctx.stroke();
      }
    }
    nodesRef.current = list;
    ctx.restore();
  };

  const scheduleDraw = () => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => { rafRef.current = 0; drawRef.current(); });
  };
  useEffect(() => { drawRef.current = draw; });
  useEffect(() => { scheduleDraw(); });
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    let t = 0;
    // On resize, recompute the fit transform so the box/handles/boundaries stay
    // centred and correctly scaled. This only touches the view (pan/zoom); the
    // manual affine params and undo history are left untouched.
    const ro = new ResizeObserver(() => {
      if (t) clearTimeout(t);
      t = setTimeout(() => { fitView(); scheduleDraw(); }, 120);
    });
    ro.observe(cv);
    return () => { if (t) clearTimeout(t); ro.disconnect(); };
  }, [meta]);

  // keep the live params ref synced with committed state; report dirty to wizard
  useEffect(() => { paramsRef.current = params; scheduleDraw(); }, [params]);
  useEffect(() => { onDirty && onDirty(isDirty()); }, [params, gridStat]);
  useEffect(() => () => { onDirty && onDirty(false); }, []);
  useEffect(() => { scheduleDraw(); }, [view, mode, bg]);

  // ---------- build the local warp lattice once the bundle extent is known ------
  // The lattice is a pure function of that extent (identical to
  // pipeline_api.warp_grid_nodes / warp_grid_radius), and a previously confirmed
  // warp is restored on top of it so re-entering curation shows the accepted
  // deformation rather than a reset grid.
  useEffect(() => {
    if (!meta) return;
    const extent = [meta.lr0, meta.lr1, meta.si0, meta.si1];
    const { baked, live } = splitLayers(job?.manual?.warp);
    // Baked layers are rebuilt on the SAME regular lattice this extent implies,
    // which is what the backend assumes too; a layer whose src no longer matches
    // is dropped rather than applied in the wrong place.
    const regular = gridNodes(extent, GRID_ROWS, GRID_COLS);
    layersRef.current = baked.map((ly) => {
      const rows = Number(ly.rows) || GRID_ROWS, cols = Number(ly.cols) || GRID_COLS;
      const src = gridNodes(extent, rows, cols);
      if (ly.src.length !== src.length || ly.dst.length !== src.length) return null;
      const R = Number(ly.R) > 0 ? Number(ly.R) : gridRadius(extent, rows, cols);
      const dst = ly.dst.map((q) => [Number(q[0]), Number(q[1])]);
      return { src, dst, R, rows, cols, fac: buildWarp(src, R),
               dispFrac: Number(ly.disp_frac) > 0 ? Number(ly.disp_frac) : null };
    }).filter(Boolean);
    setNLayers(layersRef.current.length);
    const g = buildGrid(live, extent);
    gridRef.current = { ...g, fac: buildWarp(g.src, g.R) };
    baseDstRef.current = copyNodes(g.dst);
    baselineDimsRef.current = { rows: g.rows, cols: g.cols };
    applyStat(validateGrid(g.src, g.dst, g.R, extent, g.rows, g.cols, g.dispFrac));
    setDims({ rows: g.rows, cols: g.cols, restored: g.restored });
    scheduleDraw();
  }, [meta]);

  // ---------- load bundle meta + density image once ----------
  useEffect(() => {
    jget("/api/bundle" + pq).then((m) => {
      setMeta(m);
      const img = new Image();
      img.onload = () => scheduleDraw();
      img.src = API + "/api/density.png" + pq;
      imgRef.current = img;
      // plane-aligned DAPI (the default background). Only requested when the job
      // has one; on any load error we silently stay on density.
      if (m.has_dapi) {
        const d = new Image();
        d.onload = () => scheduleDraw();
        d.onerror = () => { dapiRef.current = null; setBg("density"); };
        // Keyed on the BUNDLE's fingerprint, not the job's timestamp: jobs on
        // one section share a bundle that each registration overwrites in
        // place, so another job's registration can change this image while this
        // job's `updated` stands still. These responses carry max-age=3600.
        d.src = API + "/api/dapi.png" + pq
          + `&v=${encodeURIComponent(m.fp || job.updated || "")}`;
        dapiRef.current = d;
      } else {
        setBg("density");
      }
    }).catch((e) => setErr(String(e)));
  }, []);

  // Backgrounds that are rendered on demand by the server. DAPI and cell density
  // are fetched up front; these two cost a table scan, so they load on first use
  // and are then held in a ref.
  const LAZY_BG = {
    transcripts: { ref: () => transcriptRef, url: "/api/transcript_density.png",
                   err: "Transcript density could not be generated for this experiment." },
    markers: { ref: () => markerRef, url: "/api/marker_density.png",
               err: "Marker-gene background could not be generated for this experiment.",
               // `v` for the same reason as the DAPI above: this response is
               // cached for an hour and its URL does not otherwise change when
               // the bundle underneath it is overwritten.
               extra: () => `&v=${encodeURIComponent(meta?.fp || "")}`
                 + (markerOnRef.current
                   ? `&regions=${encodeURIComponent(markerOnRef.current.join(","))}` : "") },
  };

  // Toggle one division in the marker background. The composite is rendered
  // server-side, so a toggle means re-fetching it; the server caches per
  // selection, so flicking a division off and back on is instant after the
  // first time.
  const toggleMarkerRegion = (acro) => {
    const all = markerLegend.map((r) => r.acronym);
    const cur = markerOnRef.current || all;
    const next = cur.includes(acro) ? cur.filter((a) => a !== acro) : [...cur, acro];
    const ordered = all.filter((a) => next.includes(a));   // keep legend order
    markerOnRef.current = ordered.length === all.length ? null : ordered;
    setMarkerOn(markerOnRef.current);
    markerRef.current = null;                              // force a re-render
    if (ordered.length === 0) { scheduleDraw(); return; }  // nothing selected
    selectBackground("markers");
  };

  const selectBackground = (next) => {
    bgChoiceRef.current = next;
    const lazy = LAZY_BG[next];
    if (!lazy) {
      setBg(next);
      setBgLoading(false);
      scheduleDraw();
      return;
    }
    const slot = lazy.ref();
    if (slot.current?.complete && slot.current?.naturalWidth) {
      setBg(next);
      scheduleDraw();
      return;
    }
    setBgLoading(true);
    const image = new Image();
    image.onload = () => {
      slot.current = image;
      setBgLoading(false);
      if (bgChoiceRef.current === next) setBg(next);
      scheduleDraw();
    };
    image.onerror = () => {
      slot.current = null;
      setBgLoading(false);
      setErr(lazy.err);
    };
    image.src = API + lazy.url + pq + (lazy.extra ? lazy.extra() : "")
      + `&v=${encodeURIComponent(job.updated || "")}`;
    slot.current = image;
  };

  // Show/hide one region in the current view. Purely client-side: these
  // boundaries are already in memory, unlike the marker background which is
  // composited server-side and has to be re-fetched on every toggle.
  // The ref is written here rather than inside the setState updater: an updater
  // must be pure, and React may call it twice (StrictMode) -- which would make a
  // toggle mutate the ref twice and land on the wrong state.
  const applyHidden = (next) => {
    regionHiddenRef.current = next;
    setRegionHidden(next);
    scheduleDraw();
  };
  const toggleRegion = (name) => {
    const cur = new Set(regionHiddenRef.current[regionView] || []);
    if (cur.has(name)) cur.delete(name); else cur.add(name);
    applyHidden({ ...regionHiddenRef.current, [regionView]: cur });
  };
  const showAllRegions = () =>
    applyHidden({ ...regionHiddenRef.current, [regionView]: new Set() });
  const hiddenCount = (regionHidden[regionView] || new Set()).size;

  const showHierarchy = (mode, payload) => {
    hierarchyFeatsRef.current[mode] = payload.features || [];
    hierarchyLabelsRef.current[mode] = payload.labels || [];
    featsRef.current = hierarchyFeatsRef.current[mode];
    labelsRef.current = hierarchyLabelsRef.current[mode];
    // The legend is the UNION of what is labelled on the map and what is merely
    // drawn, because the pills are now controls: a region with no pill cannot be
    // switched off. Labels are only placed on components above
    // CURATION_MIN_LABEL_AREA_UM2, so building from them alone left 11 of the 20
    // drawn regions at depth 4 with no way to clear their fill.
    //
    // The order is total drawn area, descending, which the backend now measures
    // per component on the same cleaned raster it traced the rings from -- no
    // area is computed here. Summing the components of one acronym is the right
    // key because the pill toggles all of them at once; ordering by the largest
    // single component (what the label payload is sorted by) put mfbs below VL
    // at depth 2 even though mfbs covers half again as much of the section.
    const labelled = new Map();
    for (const label of labelsRef.current) {
      if (!labelled.has(label.acronym)) labelled.set(label.acronym, label.short);
    }
    const byAcronym = new Map();
    for (const f of featsRef.current) {
      if (f.kind !== "region") continue;
      const prev = byAcronym.get(f.acronym);
      if (prev) { prev.area += f.area_um2 || 0; continue; }
      byAcronym.set(f.acronym, {
        acronym: f.acronym,
        // features carry no `short`; at these depths it equals the acronym
        short: labelled.get(f.acronym) || f.acronym,
        color: f.color, name: f.name,
        labelled: labelled.has(f.acronym), area: f.area_um2 || 0,
      });
    }
    setLegend([...byAcronym.values()].sort((a, b) => b.area - a.area));
  };

  const selectRegionView = (next) => {
    regionChoiceRef.current = next;
    if (next === "alignment") {
      featsRef.current = alignmentFeatsRef.current;
      labelsRef.current = [];
      setRegionView(next);
      setRegionLoading(null);
      hoverRegionRef.current = null;
      scheduleDraw();
      return;
    }
    const cached = hierarchyFeatsRef.current[next];
    if (cached) {
      showHierarchy(next, {
        features: cached, labels: hierarchyLabelsRef.current[next] || [],
      });
      setRegionView(next);
      hoverRegionRef.current = null;
      scheduleDraw();
      return;
    }
    setRegionLoading(next);
    jget(`/api/boundaries?depth=${next}&path=${encodeURIComponent(bundlePath)}`)
      .then((raw) => {
        // rank palette, not Allen's: too dim to align against (see regionColors.js)
        const rc = rankRecolor(raw.features, raw.labels);
        const payload = { ...raw, features: rc.features, labels: rc.labels };
        hierarchyFeatsRef.current[next] = payload.features || [];
        hierarchyLabelsRef.current[next] = payload.labels || [];
        if (regionChoiceRef.current === next) {
          showHierarchy(next, payload);
          setRegionView(next);
          setRegionLoading(null);
          hoverRegionRef.current = null;
          scheduleDraw();
        }
      })
      .catch((e) => {
        if (regionChoiceRef.current === next) {
          setRegionLoading(null);
          setErr(String(e));
        }
      });
  };

  // Load sparse guides plus depth 2. Deeper hierarchy levels remain lazy so
  // entering Curation is not delayed by polygons the user may never request.
  useEffect(() => {
    if (!meta) return;
    Promise.all([
      jget(`/api/boundaries?depth=alignment&path=${encodeURIComponent(bundlePath)}`),
      jget(`/api/boundaries?depth=depth2&path=${encodeURIComponent(bundlePath)}`),
    ]).then(([guidesRaw, allRaw]) => {
      // both views in the rank palette, each ranked by its own region areas
      const guides = { ...guidesRaw, features: rankRecolor(guidesRaw.features).features };
      const rc2 = rankRecolor(allRaw.features, allRaw.labels);
      const all = { ...allRaw, features: rc2.features, labels: rc2.labels };
      alignmentFeatsRef.current = guides.features || [];
      hierarchyFeatsRef.current.depth2 = all.features || [];
      hierarchyLabelsRef.current.depth2 = all.labels || [];
      featsRef.current = alignmentFeatsRef.current;
      labelsRef.current = [];
      const guideSeen = new Map();
      for (const f of alignmentFeatsRef.current) {
        if (f.kind !== "alignment_region" || guideSeen.has(f.guide_family)) continue;
        guideSeen.set(f.guide_family, {
          acronym: f.guide_family, name: f.name, optional: !!f.optional,
          color: f.color, members: f.members || [], components: 1,
        });
      }
      for (const f of alignmentFeatsRef.current) {
        const item = guideSeen.get(f.guide_family);
        if (item && f.component > item.components) item.components = f.component;
      }
      setAlignmentLegend([...guideSeen.values()]);
      const seen = new Map();
      for (const L of hierarchyLabelsRef.current.depth2)
        if (!seen.has(L.acronym))
          seen.set(L.acronym, { acronym: L.acronym, short: L.short, color: L.color, name: L.name });
      setLegend([...seen.values()]);
      if (!viewRef.current.fitted) fitView();
      scheduleDraw();
    }).catch((e) => setErr(String(e)));
  }, [meta]);

  const fitView = () => {
    const cv = canvasRef.current;
    if (!cv || !meta) return;
    const cw = cv.clientWidth, ch = cv.clientHeight;
    const s = Math.min(cw / (meta.lr1 - meta.lr0), ch / (meta.si1 - meta.si0)) * 0.9;
    viewRef.current = {
      scale: s,
      tx: cw / 2 - s * (meta.lr0 + meta.lr1) / 2,
      ty: ch / 2 - s * (meta.si0 + meta.si1) / 2,
      fitted: true,
    };
  };

  // ---------- history ----------
  // One snapshot per completed interaction (a box drag OR a node drag), holding
  // BOTH the affine params and the grid, so Undo walks back through the two kinds
  // of edit in the order they were actually made.
  const pushHistory = () => {
    const g = gridRef.current;
    // Tools that carry undoable state of their own contribute a snapshot under
    // their id, so history stays correct without the host knowing what is in it.
    const tools = {};
    for (const t of TOOLS) if (t.snapshot) tools[t.id] = t.snapshot(editor(t.id));
    histRef.current.push({
      params: { ...paramsRef.current },
      dst: g ? copyNodes(g.dst) : null,
      tools,
    });
    if (histRef.current.length > 50) histRef.current.shift();
    setHistLen(histRef.current.length);
  };
  const undo = () => {
    // A tool may consume the undo itself -- cancelling a half-finished gesture
    // should not also roll back the last committed edit.
    const active = getTool(localToolRef.current);
    if (active.interceptUndo && active.interceptUndo(editor(active.id))) return;
    const snap = histRef.current.pop();
    if (!snap) return;
    setHistLen(histRef.current.length);
    const g = gridRef.current;
    if (g && snap.dst) { g.dst = copyNodes(snap.dst); revalidate(); }
    for (const t of TOOLS)
      if (t.restore && snap.tools && snap.tools[t.id])
        t.restore(editor(t.id), snap.tools[t.id]);
    setWarpMsg(null);
    setParams(snap.params);
  };
  // Reset local: drop the grid displacement only, keeping the global affine.
  const resetLocal = () => {
    const g = gridRef.current;
    if (!g) return;
    pushHistory();
    g.dst = copyNodes(g.src);
    resetToolState();
    revalidate();
    setWarpMsg(null);
    scheduleDraw();
  };
  // Reset all: affine back to identity AND the grid back to identity.
  const resetAll = () => {
    const g = gridRef.current;
    histRef.current = []; setHistLen(0);
    if (g) { g.dst = copyNodes(g.src); revalidate(); }
    resetToolState();
    setWarpMsg(null);
    setParams({ ...IDENTITY_PARAMS });
  };
  useEffect(() => { scheduleDraw(); }, [regionLineScale, regionFillAlpha]);

  // Change lattice density without ever resampling an existing displacement
  // field. Global affine is preserved; local offsets are explicitly reset.
  // The whole stack as the backend will see it: baked layers, then the live grid
  // if it deforms anything.
  const allLayers = () => {
    const g = gridRef.current;
    const live = g && nMovedOf(g.src, g.dst) > 0
      ? [{ src: g.src, dst: g.dst, R: g.R, fac: g.fac, rows: g.rows,
           cols: g.cols, dispFrac: g.dispFrac }]
      : [];
    return [...layersRef.current, ...live];
  };

  const addLayer = () => {
    const g = gridRef.current;
    if (!g || nMovedOf(g.src, g.dst) === 0) {
      setWarpMsg("Nothing to bake — drag something first.");
      return;
    }
    if (layersRef.current.length >= MAX_LAYERS - 1) {
      setWarpMsg(`${MAX_LAYERS} layers is the limit; finalize inverts one more `
               + `pass per layer. Remove one, or confirm what you have.`);
      return;
    }
    // Gate the STACK, not just this layer: determinants multiply, so layers that
    // are each fine can still compose into a fold. Checked here so the refusal
    // lands on the action that caused it rather than on Confirm.
    const stack = allLayers();
    const det = composedJacMin(stack, g.extent);
    if (det <= COMPOSED_JAC_MIN) {
      setWarpMsg(`Baking this would take the stack to min |J| ${det.toFixed(3)}, `
               + `at or below the ${COMPOSED_JAC_MIN} floor. Undo some of this `
               + `layer first.`);
      return;
    }
    layersRef.current = stack;
    setNLayers(layersRef.current.length);
    // fresh identity grid, same dimensions, in the now-deformed space
    g.dst = copyNodes(g.src);
    baseDstRef.current = copyNodes(g.src);
    histRef.current = []; setHistLen(0);
    resetToolState();
    setWarpMsg(`Layer ${layersRef.current.length} baked; the grid is reset so you `
             + `can keep going. Undo no longer reaches past this point.`);
    revalidate();
    scheduleDraw();
    onDirty && onDirty(true);
  };

  const removeLayer = () => {
    if (!layersRef.current.length) return;
    if (!window.confirm(
      `Remove layer ${layersRef.current.length}? Its correction is discarded.`)) return;
    layersRef.current = layersRef.current.slice(0, -1);
    setNLayers(layersRef.current.length);
    histRef.current = []; setHistLen(0);
    setWarpMsg(`Layer removed; ${layersRef.current.length} remain.`);
    revalidate();
    scheduleDraw();
    onDirty && onDirty(true);
  };

  const useGridSize = (rows, cols) => {
    const g = gridRef.current;
    if (!g || (g.rows === rows && g.cols === cols)) return;
    // No confirmation: switching density is an exploratory move and a dialog on
    // every press made it feel consequential. The cost is real but small -- the
    // local correction is reset, and because the saved snapshots hold dst for
    // the OLD dimensions, undo cannot cross the change either. The toast below
    // says so after the fact rather than asking first.
    const hadLocalWork = nMovedOf(g.src, g.dst) > 0 || anyToolDirty();
    histRef.current = []; setHistLen(0);
    const src = gridNodes(g.extent, rows, cols);
    g.rows = rows;
    g.cols = cols;
    g.src = src;
    g.dst = copyNodes(src);
    g.R = gridRadius(g.extent, rows, cols);
    g.fac = buildWarp(g.src, g.R);
    setDims({ rows, cols, restored: false });
    resetToolState();
    setWarpMsg(hadLocalWork
      ? `Using ${rows}×${cols} grid — the local correction was reset and cannot `
        + `be undone across the change.`
      : `Using ${rows}×${cols} grid.`);
    revalidate();
    scheduleDraw();
  };

  // ---------- pointer interaction ----------
  const hitHandle = (sx, sy) => {
    let best = null, bd = HIT_PX;
    for (const h of handlesRef.current) {
      const d = Math.hypot(sx - h.pt[0], sy - h.pt[1]);
      if (d < bd) { best = h; bd = d; }
    }
    return best;
  };
  // nearest DRAGGABLE warp-grid node within the grab tolerance. Everything but the
  // 4 corners is draggable now; edge nodes are simply constrained to their own axis.
  const hitNode = (sx, sy) => {
    let best = null, bd = HIT_PX;
    for (const nd of nodesRef.current) {
      if (nd.kind === NODE_CORNER) continue;
      const d = Math.hypot(sx - nd.pt[0], sy - nd.pt[1]);
      if (d < bd) { best = nd; bd = d; }
    }
    return best;
  };

  // The region under the cursor, with its rings expressed in LATTICE space.
  // applyAffine ONLY: src/dst live in the pre-warp frame, so mapping through
  // place() (affine THEN warp) would measure distances in the wrong space and the
  // rigid core would drift off the anatomy as the warp grew.
  const grabRegionAt = (sx, sy) => {
    const M = matrix();
    for (let i = regionHitsRef.current.length - 1; i >= 0; i--) {
      const f = regionHitsRef.current[i];
      const ext = f.rings.find((r) => r.role === "exterior");
      const holes = f.rings.filter((r) => r.role === "hole");
      if (!ext || !pointInRing(sx, sy, ext.points)) continue;
      if (holes.some((r) => pointInRing(sx, sy, r.points))) continue;
      if (!f.dataRings || !f.dataRings.length) continue;
      return {
        acronym: f.acronym,
        rings: f.dataRings.map((r) => ({
          role: r.role,
          points: (r.points || []).map((pt) => applyAffine(M, pt)),
        })),
      };
    }
    return null;
  };

  // ---------- the editor context handed to correction tools ----------
  // This object is the ENTIRE API a tool may use. Keeping it explicit is the
  // point: a tool cannot reach into the component, so a tool can be added or
  // deleted without reading the rest of this file. See curation/tools/README.md.
  regionHiddenRef.current = regionHidden;
  // Which name a feature is filtered by. The Landmarks view groups several Allen
  // structures under one guide (HPF covers CA1, DG, ...), so its legend keys on
  // guide_family; the depth views key on the rolled-up acronym.
  const regionKey = (f) => f.guide_family || f.acronym;
  const isRegionHidden = (name) => !!regionHiddenRef.current[regionView]?.has(name);

  toolStateRef.current = toolState;
  localToolRef.current = localTool;
  const activeTool = () => getTool(localToolRef.current);

  const popHistory = () => {
    histRef.current.pop();
    setHistLen(histRef.current.length);
  };
  const setToolSt = (id, patch) => {
    const cur = toolStateRef.current;
    const next = { ...cur, [id]: { ...cur[id], ...patch } };
    toolStateRef.current = next;
    setToolState(next);
  };
  const resetToolState = () => {
    const next = initialToolState();
    toolStateRef.current = next;
    setToolState(next);
  };
  // `extra` lets draw() hand in the frame-local warp mapper without every tool
  // having to rebuild it.
  const editor = (id, extra) => ({
    id,
    st: toolStateRef.current[id] || {},
    setSt: (patch) => setToolSt(id, patch),
    grid: () => gridRef.current,
    dragRef,
    toUm, evtUm, toScreen, matrix, copyNodes,
    warpOnly: (pt) => pt,                       // overridden during draw()
    hitNode, grabRegionAt,
    pushHistory, undo, popHistory, revalidate, scheduleDraw, setWarpMsg,
    setCursor: (c) => { if (canvasRef.current) canvasRef.current.style.cursor = c; },
    dims, nMovable, useGridSize,
    ...(extra || {}),
  });
  const anyToolDirty = () => TOOLS.some(
    (t) => (t.dirty ? t.dirty(editor(t.id)) : false));

  const onPointerDown = (evt) => {
    evt.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const sx = evt.clientX - rect.left, sy = evt.clientY - rect.top;
    const v = viewRef.current;
    // 'before' (auto alignment) is read-only: pan only
    if (view !== "after") {
      panRef.current = { x0: evt.clientX, y0: evt.clientY, tx0: v.tx, ty0: v.ty };
      return;
    }
    // local warp mode: the ONLY editable things are the interior grid nodes.
    // The affine box is not editable here, so anything else pans the view.
    if (mode === "local") {
      if (evt.shiftKey) {
        panRef.current = { x0: evt.clientX, y0: evt.clientY, tx0: v.tx, ty0: v.ty };
        return;
      }
      // Correction tools get first refusal, in registry order. A tool returns
      // true when it consumed the press; falling through pans the view. The host
      // no longer knows which tools exist.
      if (activeTool().onDown?.(editor(localTool), evt, sx, sy)) return;
      panRef.current = { x0: evt.clientX, y0: evt.clientY, tx0: v.tx, ty0: v.ty };
      return;
    }
    const h = hitHandle(sx, sy);
    if (h) {
      pushHistory();
      const which = h.type === "corner" ? "both" : h.type;
      dragRef.current = {
        type: h.type, which, signX: h.signX || 0, signY: h.signY || 0,
        p0: { ...paramsRef.current },
      };
      canvasRef.current.style.cursor = h.type === "rot" ? "grabbing" : "nwse-resize";
      return;
    }
    // inside the box body -> translation
    const { corners } = computeHandles();
    if (corners.length && pointInQuad([sx, sy], corners)) {
      pushHistory();
      dragRef.current = { type: "move", downUm: toUm(sx, sy), p0: { ...paramsRef.current } };
      canvasRef.current.style.cursor = "grabbing";
      return;
    }
    // empty space -> pan
    panRef.current = { x0: evt.clientX, y0: evt.clientY, tx0: v.tx, ty0: v.ty };
  };

  const onPointerMove = (evt) => {
    const drag = dragRef.current;
    // The active tool handles its own drag. Anything it declines falls through
    // to the affine box and panning below.
    if (drag && activeTool().onMove?.(editor(localTool), evt)) return;
    if (drag) {
      const um = evtUm(evt);
      const g = geom();
      const p = { ...drag.p0 };
      const ct = [g.cx + p.tx, g.cy + p.ty];
      if (drag.type === "move") {
        p.tx = drag.p0.tx + (um[0] - drag.downUm[0]);
        p.ty = drag.p0.ty + (um[1] - drag.downUm[1]);
      } else if (drag.type === "rot") {
        p.rotDeg = rotationFromHandle(um, ct);
      } else {
        const s = anchoredScaleFromHandle(
          drag.which, um, [g.cx, g.cy], drag.p0,
          g.halfW, g.halfH, drag.signX, drag.signY,
        );
        p.sx = s.sx; p.sy = s.sy; p.tx = s.tx; p.ty = s.ty;
      }
      paramsRef.current = p;
      scheduleDraw();
      return;
    }
    if (panRef.current) {
      const pn = panRef.current, v = viewRef.current;
      v.tx = pn.tx0 + (evt.clientX - pn.x0);
      v.ty = pn.ty0 + (evt.clientY - pn.y0);
      scheduleDraw();
      return;
    }
    // hover cursor feedback over handles / grid nodes
    if (view === "after") {
      const rect = canvasRef.current.getBoundingClientRect();
      const mx = evt.clientX - rect.left, my = evt.clientY - rect.top;
      let hovered = null;
      for (let i = regionHitsRef.current.length - 1; i >= 0; i--) {
        const feature = regionHitsRef.current[i];
        const exterior = feature.rings.find((ring) => ring.role === "exterior");
        const holes = feature.rings.filter((ring) => ring.role === "hole");
        if (exterior && pointInRing(mx, my, exterior.points)
          && !holes.some((ring) => pointInRing(mx, my, ring.points))) {
          hovered = feature.acronym;
          break;
        }
      }
      if (hovered !== hoverRegionRef.current) {
        hoverRegionRef.current = hovered;
        scheduleDraw();
      }
      if (mode === "local") {
        canvasRef.current.style.cursor =
          activeTool().cursor?.(editor(localTool), mx, my) || "default";
      } else {
        const h = hitHandle(mx, my);
        canvasRef.current.style.cursor = h
          ? (h.type === "rot" ? "grab" : "nwse-resize") : "default";
      }
    }
  };

  const onPointerUp = (evt) => {
    const drag = dragRef.current;
    // Tools get the release first; each rolls back or trims its own gesture.
    if (drag && activeTool().onUp?.(editor(localTool), evt)) {
      // handled by the tool
    } else if (drag) {
      dragRef.current = null;
      setParams({ ...paramsRef.current }); // commit -> re-render + dirty flag
    }
    panRef.current = null;
    if (canvasRef.current) canvasRef.current.style.cursor = "default";
    scheduleDraw();
  };

  // Keep active drags alive even when the browser does not honour canvas
  // pointer capture consistently, or the pointer briefly leaves the canvas.
  // This is especially important for direct line pulls in embedded browsers.
  pointerMoveRef.current = onPointerMove;
  pointerUpRef.current = onPointerUp;
  useEffect(() => {
    const move = (evt) => {
      if (dragRef.current || panRef.current) pointerMoveRef.current?.(evt);
    };
    const up = (evt) => {
      if (dragRef.current || panRef.current) pointerUpRef.current?.(evt);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    // Some embedded Chromium surfaces synthesize pointerdown but only continue
    // the drag with legacy mousemove/mouseup events.
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, []);

  const onWheel = (evt) => {
    evt.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const sx = evt.clientX - rect.left, sy = evt.clientY - rect.top;
    const v = viewRef.current;
    const um = toUm(sx, sy);
    const k = Math.exp(-evt.deltaY * 0.0015);
    v.scale *= k;
    v.tx = sx - um[0] * v.scale;
    v.ty = sy - um[1] * v.scale;
    scheduleDraw();
  };

  // ---------- confirm ----------
  const confirm = async () => {
    if (!meta) return;
    setBusy(true); setErr(null);
    try {
      const g = geom(); const p = paramsRef.current;
      const affine = {
        cx: g.cx, cy: g.cy,
        tx: p.tx, ty: p.ty, rot_deg: p.rotDeg, sx: p.sx, sy: p.sy,
      };
      // send the grid only when it actually deforms something; the backend
      // re-validates it and treats an identity grid as pure affine anyway.
      const gr = gridRef.current;
      // disp_frac travels with the warp so the backend validates it against the
      // budget it was authored at. Omitted entirely for grids built by the affine,
      // direct-drag and node tools, which keeps them on the historical cap.
      const stack = allLayers();
      const asLayer = (ly) => ({
        algo: GRID_ALGO, rows: ly.rows, cols: ly.cols, R: ly.R,
        src: copyNodes(ly.src), dst: copyNodes(ly.dst),
        ...(ly.dispFrac ? { disp_frac: ly.dispFrac } : {}),
      });
      // One layer is sent in the historical single-lattice shape, so a curation
      // that never used layering persists exactly as it always did.
      const warp = stack.length === 0 ? null
        : stack.length === 1 ? asLayer(stack[0])
        : { layers: stack.map(asLayer) };
      const updated = await jpost(`/api/jobs/${job.id}/curation`, { affine, warp });
      baselineRef.current = { ...p };
      if (gr) {
        baseDstRef.current = copyNodes(gr.dst);
        baselineDimsRef.current = { rows: gr.rows, cols: gr.cols };
      }
      onDirty && onDirty(false);
      if (onComplete) onComplete(updated);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  // dirty / reset availability span BOTH the affine and the grid
  const gridDirty = () => {
    const g = gridRef.current;
    const dimensionChanged = !!(g && baselineDimsRef.current &&
      (g.rows !== baselineDimsRef.current.rows || g.cols !== baselineDimsRef.current.cols));
    return dimensionChanged || !!(g && baseDstRef.current &&
      baseDstRef.current.length === g.dst.length && nMovedOf(baseDstRef.current, g.dst) > 0);
  };
  const isDirty = () => !paramsEqual(params, baselineRef.current) || gridDirty();
  const dirty = isDirty();
  const gridMoved = gridStat.nMoved > 0;
  const nMovable = movableCount(dims.rows, dims.cols);   // all but the 4 corners
  const canReset = !isIdentity(params) || gridMoved || histLen > 0
    || anyToolDirty();
  // Why the active tool says Confirm is not safe yet, or null.
  const toolBlock = activeTool().blocks?.(editor(localTool)) || null;
  // Capitalised binding because JSX needs a component identifier, not a call.
  const ToolPanel = activeTool().Panel;
  const toolBadges = activeTool().badges?.(editor(localTool)) || [];
  const apBad = job.ap_idx != null && meta && (
    meta.ap !== job.ap_idx ||
    meta.sampled_ap_idx == null ||
    meta.sampled_ap_idx !== job.ap_idx
  );

  return (
    <>
      <div className="sidebar">
        <h1>Manual curation</h1>
        {/* Step 4 hands over on its own the moment registration finishes, so its
            result would otherwise flash past unread. This is where the user
            actually lands, so the number lands here too. */}
        {reg.n_cells > 0 && (
          <div className="muted">
            Automatic registration placed{" "}
            <b>{(reg.n_mapped || 0).toLocaleString()}</b> of{" "}
            {reg.n_cells.toLocaleString()} cells (
            {(reg.coverage * 100).toFixed(1)}%) inside an Allen region. That is the
            atlas fit alone — refining those region boundaries is what this step is
            for.
            {overhang && (
              <> {overhang.n.toLocaleString()} of the rest ({overhang.pct}) lie past the
                end of the Allen atlas ({overhang.ends}); no curation can label
                those.</>
            )}
          </div>
        )}
        <div className="muted" style={{ marginTop: 8 }}>
          First fit the atlas globally. For local correction, click a point on the
          atlas outline and then the matching point on the tissue. The existing
          validated warp is solved automatically.
        </div>

        {/* The AP / annotation-AP / bundle-tag badges were removed on 2026-09-16:
            all three are reported in step 6, and none of them is something you
            act on while curating. The apBad warning below still quotes every one
            of them, because that is the one moment they matter. */}
        {apBad && (
          <div className="result" style={{ color: "#f85149", marginTop: 8 }}>
            AP mismatch: selected AP {job.ap_idx}, bundle AP {meta.ap}, annotation
            AP {meta.sampled_ap_idx ?? "unknown"}. The bundle is stale — go back and re-run
            registration before curating.
          </div>
        )}

        <h2>
          Alignment
          <HelpTip label="About the alignment views">
            <p><b>Auto:</b> Automatic alignment (read-only)</p>
            <p><b>Manual:</b> Refine alignment by manual curation</p>
          </HelpTip>
        </h2>
        <div className="seg">
          <button className={view === "before" ? "active" : ""}
                  onClick={() => setView("before")}>Auto</button>
          <button className={view === "after" ? "active" : ""}
                  onClick={() => setView("after")}>Manual</button>
        </div>

        {view === "after" && (
          <>
            <h2>
              Edit mode
              <HelpTip label="About the edit modes">
                <p><b>Global affine:</b> Drag the box to translate; edges scale one
                  axis (fix vertical squash with the top/bottom handles); corners
                  scale both; the top knob rotates.</p>
                <p><b>Local correction:</b> see each Correction tool section.</p>
                {/* Stated once, here, rather than repeated in three of the four
                    tool blurbs -- where it read as a per-tool feature and was
                    still missing from the fourth. Checked against the handlers:
                    onWheel has no mode guard at all, and the pointer-down path
                    falls through to a pan whenever nothing else claims the
                    press, which in Local correction means after the tool has
                    refused it. */}
                <p>The wheel zooms in every mode, and dragging empty space pans.
                  In Local correction the tool gets the press first, so hold
                  <b> shift</b> to pan over the tissue.</p>
              </HelpTip>
            </h2>
            <div className="seg">
              <button className={mode === "global" ? "active" : ""}
                      onClick={() => {
                        // leaving local mode abandons any half-finished gesture
                        resetToolState();
                        setMode("global");
                      }}>Global affine</button>
              <button className={mode === "local" ? "active" : ""}
                      onClick={() => setMode("local")}>Local correction</button>
            </div>
            {mode === "local" && (
              <>
                <h2>
                  Correction tool
                  {/* Built from the registry, not written out here, so adding a
                      tool documents it automatically and the wording cannot
                      drift from the tool it describes. */}
                  <HelpTip label="About the correction tools">
                    {TOOLS.map((t) => (
                      <p key={t.id}><b>{t.label}:</b> {t.blurb}</p>
                    ))}
                  </HelpTip>
                </h2>
                {/* Order agreed 2026-11-03: warp layers, grid density, then the
                    tool buttons -- the order a user weighs them in. */}
                {/* Layers and the undo/reset row sit with the correction tool, above
                    the region sets, because they act ON the correction: how many passes
                    it is made of, and how to take one back. Below the region and overlay
                    controls they read as view settings, which is what they are not. */}
                {/* One lattice can only bend so far before the fold gate stops it;
                    baking the current one and carrying on with a fresh grid reaches
                    further WITHOUT pushing any single layer harder, because finalize
                    inverts them one at a time. */}
                <div className="display-control">
                  <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                    WARP LAYERS — {nLayers} baked{nLayers ? " + the live grid" : ""}
                    <HelpTip label="About warp layers">
                      <p>Local correction moves the nodes of a warp grid, and each
                        node can move only so far: the limit is shown under Grid
                        density. A move past it is refused with "displacement
                        exceeds limit".</p>
                      <p><b>Add layer</b> bakes the current grid into a layer and
                        lays a fresh grid over the corrected section, so you can
                        keep correcting from where you are. When a move is
                        refused, add a layer and carry on.</p>
                      <p><b>Remove last</b> discards the most recently baked
                        layer. Undo stops at a layer boundary. Up to {MAX_LAYERS}{" "}
                        layers in all, counting the live grid.</p>
                    </HelpTip>
                  </div>
                  <div className="row" style={{ gap: 6, margin: 0 }}>
                    <button style={{ flex: 1, whiteSpace: "nowrap" }}
                            onClick={addLayer}
                            disabled={nLayers >= MAX_LAYERS - 1}
                            title={nLayers >= MAX_LAYERS - 1
                              ? `${MAX_LAYERS} layers is the limit`
                              : "Bake the current grid and start a fresh one on top, to "
                                + "keep correcting past this grid's limit"}>
                      Add layer
                    </button>
                    <button style={{ flex: 1, whiteSpace: "nowrap" }}
                            onClick={removeLayer} disabled={!nLayers}
                            title="Discard the most recently baked layer">
                      Remove last
                    </button>
                  </div>
                  {nLayers > 0 && (
                    <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                      Undo stops at the layer boundary — use <b>Remove last</b> to go
                      back further.
                    </div>
                  )}
                </div>

                {/* Lattice density — grouped with the correction tool because it acts
                    on the correction: the per-node cap is a fraction of node spacing, so
                    a coarser lattice reaches further while the strain, and therefore the
                    fold gate, barely moves. What is traded is local detail, which is a
                    judgement about the tissue and so is offered rather than defaulted. */}
                <div className="display-control">
                  <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                    GRID DENSITY — reach vs local detail
                  </div>
                  <div className="seg" style={{ width: "100%" }}>
                    {GRID_PRESETS.map((g) => (
                      <button key={`${g.rows}x${g.cols}`}
                              className={dims.rows === g.rows && dims.cols === g.cols
                                         ? "active" : ""}
                              title={`${g.rows}×${g.cols} — ${g.note}.`}
                              onClick={() => useGridSize(g.rows, g.cols)}>
                        {g.rows}×{g.cols}
                      </button>
                    ))}
                  </div>
                  {meta && (
                    <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                      {dims.rows}×{dims.cols}: a node may move up to{" "}
                      <b>{Math.round(maxDisp([meta.lr0, meta.lr1, meta.si0, meta.si1],
                                             dims.rows, dims.cols))} µm</b>
                      {(GRID_PRESETS.find((g) => g.rows === dims.rows
                                              && g.cols === dims.cols) || {}).note
                        && ` — ${GRID_PRESETS.find((g) => g.rows === dims.rows
                                                && g.cols === dims.cols).note}`}.
                      {" "}<u>Switching density discards the live grid's
                      correction; it is not saved as a layer. To keep it, press
                      Add layer first, then switch.</u>
                    </div>
                  )}
                </div>

                <div className="seg">
                  {TOOLS.map((t) => (
                    <button key={t.id}
                            className={localTool === t.id ? "active" : ""}
                            onClick={() => setLocalTool(t.id)}>{t.label}</button>
                  ))}
                </div>
                {ToolPanel && <ToolPanel ed={editor(localTool)} />}
              </>
            )}
          </>
        )}
        {/* three buttons in one narrow sidebar row: nowrap + tighter horizontal
            padding keeps every label on a single line without overflowing */}
        <div className="row">
          {[["Undo", undo, !histLen && !anyToolDirty()],
            ["Reset local", resetLocal, !gridMoved && !anyToolDirty()],
            ["Reset all", resetAll, !canReset]].map(([label, fn, off]) => (
            <button key={label} onClick={fn} disabled={off}
                    style={{ flex: 1, whiteSpace: "nowrap", padding: "6px 4px" }}>
              {label}
            </button>
          ))}
        </div>
        <h2>
          Alignment regions
          <HelpTip label="About the alignment region sets">
            <p><b>Landmarks:</b> A few high-contrast structures picked for aligning
              against — not a hierarchy level, so these sit at different Allen
              depths and do not cover the whole section. Depth 2–4 do.</p>
            <p><b>Depth:</b> Closed region polygons rolled up to literal Allen
              hierarchy depth.</p>
            <p>Click a region below to clear its fill; its outline stays. Display
              only; final labels remain leaf-level.</p>
          </HelpTip>
        </h2>
        <div className="seg">
          {/* Label only -- the mode id stays "alignment". Renaming the id would
              collide with the unrelated "landmarks" tier in BOUNDARY_MODES and
              would invalidate every cached boundary set for no benefit.
              "Guides" read as the coarsest rung of the Depth 2/3/4 ladder, which
              it is not: it is four hand-picked structures at four different
              Allen depths (HPF 5, TH 4, VL 2, V3 2), not a hierarchy level. */}
          <button className={regionView === "alignment" ? "active" : ""}
                  onClick={() => selectRegionView("alignment")}>Landmarks</button>
          {[2, 3, 4].map((level) => {
            const modeName = `depth${level}`;
            return (
              <button key={modeName}
                      className={regionView === modeName ? "active" : ""}
                      disabled={!!regionLoading}
                      onClick={() => selectRegionView(modeName)}>
                {regionLoading === modeName ? "Loading…" : `Depth ${level}`}
              </button>
            );
          })}
        </div>
        {/* Pills, matching the depth legend below: same shape for the same kind
            of information. "real region" / "optional" was dropped from the face
            of each row -- it is a property of the guide SET, not something to
            weigh while aligning -- but it is kept in the hover text so the
            distinction is still available where it is wanted. The swatch is the
            structure's own Allen colour, i.e. the colour of its outline on the
            canvas. */}
        {regionView === "alignment" && alignmentLegend.length > 0 && (
          <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }}>
            {alignmentLegend.map((item) => {
              const on = !isRegionHidden(item.acronym);
              return (
                <button key={item.acronym} className="badge"
                        onClick={() => toggleRegion(item.acronym)}
                        title={`${on ? "Clear the fill on" : "Fill"} ${item.name}`
                               + `${item.optional ? " (optional)" : ""}`
                               + `${item.members.length ? ` · ${item.members.join(", ")}` : ""}`}
                        style={{
                          display: "inline-flex", alignItems: "center", gap: 5,
                          cursor: "pointer", font: "inherit",
                          borderColor: on ? (item.color || "#8aa0b8") : "var(--border)",
                          opacity: on ? 1 : 0.45,
                          textDecoration: on ? "none" : "line-through",
                        }}>
                  <span style={{
                    width: 9, height: 9, borderRadius: 2,
                    background: on ? (item.color || "#8aa0b8") : "transparent",
                    border: on ? "none" : `1px solid ${item.color || "#8aa0b8"}`,
                    display: "inline-block", flex: "0 0 auto",
                  }} />
                  {item.acronym}{item.components > 1 ? "-L / -R" : ""}
                </button>
              );
            })}
            {hiddenCount > 0 && (
              <button className="badge" style={{ cursor: "pointer", font: "inherit" }}
                      onClick={showAllRegions}>show all</button>
            )}
          </div>
        )}
        {/* The depth legend sits here rather than at the foot of the sidebar: it
            belongs to the buttons above it, and its old heading ("Regions
            (depth 3)") only repeated what the active button already said. */}
        {regionView !== "alignment" && legend.length > 0 && (
          <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }}>
            {legend.map((r) => {
              const on = !isRegionHidden(r.acronym);
              return (
                <button key={r.acronym} className="badge"
                        onClick={() => toggleRegion(r.acronym)}
                        title={`${on ? "Clear the fill on" : "Fill"} ${r.name}`
                               + (r.labelled ? "" : " — too small to label on the map")}
                        style={{
                          display: "inline-flex", alignItems: "center", gap: 5,
                          cursor: "pointer", font: "inherit",
                          borderColor: on ? r.color : "var(--border)",
                          opacity: on ? 1 : 0.45,
                          textDecoration: on ? "none" : "line-through",
                        }}>
                  <span style={{
                    width: 9, height: 9, borderRadius: 2,
                    background: on ? r.color : "transparent",
                    border: on ? "none" : `1px solid ${r.color}`,
                    display: "inline-block", flex: "0 0 auto",
                  }} />
                  {r.short}
                </button>
              );
            })}
            {hiddenCount > 0 && (
              <button className="badge" style={{ cursor: "pointer", font: "inherit" }}
                      onClick={showAllRegions}>show all</button>
            )}
          </div>
        )}

        {/* Both sliders act on the REGION overlay, so they sit with the region
            controls above rather than after the background picker, which they do
            not touch. Adjusting the fill you just toggled is then one glance
            down, not one glance past an unrelated section. */}
        <h2>Overlay display</h2>
        <div className="display-control">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span>Region line thickness</span>
            <span className="badge">{Math.round(regionLineScale * 100)}%</span>
          </div>
          {/* Reaches 0, which is the whole atlas overlay off -- no separate
              toggle. It matches Region fill opacity directly below, which already
              runs 0-100%, so the two sliders in this panel behave alike. */}
          <input type="range" min={0} max={2} step={0.1}
                 value={regionLineScale}
                 onChange={(e) => setRegionLineScale(+e.target.value)} />
        </div>
        <div className="display-control">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span>Region fill opacity</span>
            <span className="badge">{Math.round(regionFillAlpha * 100)}%</span>
          </div>
          <input type="range" min={0} max={1} step={0.05}
                 value={regionFillAlpha}
                 onChange={(e) => setRegionFillAlpha(+e.target.value)} />
        </div>
        <button style={{ width: "100%", marginTop: 7 }}
                onClick={() => { setRegionLineScale(1); setRegionFillAlpha(0.25); }}>
          Reset overlay display
        </button>

        {meta && (meta.has_dapi || meta.has_markers) && (
          <>
            <h2>
              Background
              <HelpTip label="About the background layers">
                <p><b>DAPI:</b> the full tissue outline and cortical layering.</p>
                {!meta.has_dapi && (
                  <p><b>Cell density:</b> where the segmented cells are, in
                    grayscale (dense = white). Shown when the job has no DAPI.</p>
                )}
                <p><b>Marker genes:</b> Measured expression of canonical markers,
                  each division in the same Allen colour the overlay uses — so a
                  division and its outline should sit on top of each other.
                  Brightness is total marker signal; colour is which division
                  dominates that pixel. Click a division to show or hide it, which
                  is the quickest way to check one structure against its outline.</p>
              </HelpTip>
            </h2>
            <div className="seg">
              {meta.has_dapi && (
                <button className={bg === "dapi" ? "active" : ""}
                        onClick={() => selectBackground("dapi")}>DAPI</button>
              )}
              {/* Without DAPI, density is the starting background, so it needs
                  its own button or Marker genes is a one-way trip (2026-10-21).
                  With DAPI it stays hidden, per the 2026-09-14 note below. */}
              {!meta.has_dapi && (
                <button className={bg === "density" ? "active" : ""}
                        onClick={() => selectBackground("density")}>Cell density</button>
              )}
              {/* Cell density and Transcripts are switched OFF at the user's
                  request (2026-09-14), not removed -- uncomment either button to
                  bring it back. Everything behind them is untouched:
                  /api/density.png and /api/transcript_density.png still work,
                  selectBackground still handles "density" and "transcripts", and
                  the draw path still resolves them. Only the way in is hidden.

              <button className={bg === "density" ? "active" : ""}
                      onClick={() => selectBackground("density")}>Cell density</button>
              */}
              {meta.has_markers && (
                <button className={bg === "markers" ? "active" : ""}
                        onClick={() => {
                          selectBackground("markers");
                          if (!markerLegend.length) {
                            jget(`/api/markers${pq}`)
                              .then((r) => setMarkerLegend(
                                (r.regions || []).filter((x) => x.used)))
                              .catch(() => {});
                          }
                        }}>Marker genes</button>
              )}
              {/*
              {meta.has_transcripts && (
                <button className={bg === "transcripts" ? "active" : ""}
                        disabled={bgLoading}
                        onClick={() => selectBackground("transcripts")}>
                  {bgLoading ? "Loading…" : "Transcripts"}
                </button>
              )}
              */}
            </div>
            {bg === "markers" && markerLegend.length > 0 && (
              <>
                <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  {markerLegend.map((r) => {
                    const on = !markerOn || markerOn.includes(r.acronym);
                    return (
                      <button key={r.acronym} className="badge" onClick={() => toggleMarkerRegion(r.acronym)}
                              title={`${on ? "Hide" : "Show"} ${r.name} — ${r.present.join(", ")}`}
                              style={{
                                cursor: "pointer", font: "inherit",
                                borderColor: on ? r.color : "var(--border)",
                                opacity: on ? 1 : 0.45,
                                textDecoration: on ? "none" : "line-through",
                              }}>
                        <span style={{
                          display: "inline-block", width: 9, height: 9, borderRadius: 2,
                          background: on ? r.color : "transparent",
                          border: on ? "none" : `1px solid ${r.color}`,
                          marginRight: 6, verticalAlign: -1,
                        }} />
                        {r.short} · {r.n_present}
                      </button>
                    );
                  })}
                  {markerOn && (
                    <button className="badge" style={{ cursor: "pointer", font: "inherit" }}
                            onClick={() => {
                              markerOnRef.current = null;
                              setMarkerOn(null);
                              markerRef.current = null;
                              selectBackground("markers");
                            }}>show all</button>
                  )}
                </div>
                {markerOn && markerOn.length === 0 && (
                  <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
                    Every division is hidden — click one to bring it back.
                  </div>
                )}
              </>
            )}
          </>
        )}

        <h2>Transform</h2>
        <div className="row" style={{ flexWrap: "wrap", gap: 6, fontSize: 12 }}>
          <span className="badge">Δx {params.tx.toFixed(0)} µm</span>
          <span className="badge">Δy {params.ty.toFixed(0)} µm</span>
          <span className="badge">rot {params.rotDeg.toFixed(1)}°</span>
          <span className="badge">sx {params.sx.toFixed(3)}</span>
          <span className="badge">sy {params.sy.toFixed(3)}</span>
        </div>

        <h2>Local correction</h2>
        <div className="row" style={{ flexWrap: "wrap", gap: 6, fontSize: 12 }}>
          {toolBadges.map((b) => (
            <span key={b.key} className="badge">{b.text}</span>
          ))}
          <span className="badge">
            {gridMoved ? `${gridStat.nMoved}/${nMovable} nodes moved` : "grid: identity"}
          </span>
          {gridMoved && <span className="badge">max {gridStat.maxDisp.toFixed(0)} µm</span>}
          {gridMoved && gridStat.minDet != null &&
            <span className="badge">min |J| {gridStat.minDet.toFixed(3)}</span>}
        </div>
        {dims.restored &&
         !(dims.rows === GRID_ROWS && dims.cols === GRID_COLS) && (
          <div className="result" style={{ marginTop: 8, fontSize: 12 }}>
            This older job keeps its saved {dims.rows}×{dims.cols} grid.
            <button style={{ marginTop: 8, width: "100%" }}
                    onClick={() => useGridSize(GRID_ROWS, GRID_COLS)}>
              Switch to {GRID_ROWS}×{GRID_COLS} (reset local warp)
            </button>
          </div>
        )}
        {!gridStat.ok && (
          <div className="result" style={{ color: "#f85149", marginTop: 8 }}>
            Invalid warp: {gridStat.reason}
          </div>
        )}
        {toolBlock && (
          <div className="result" style={{ color: "#d29922", marginTop: 8 }}>
            {toolBlock}
          </div>
        )}
        {warpMsg && (
          <div className="result" style={{ color: "#d29922", marginTop: 8 }}>{warpMsg}</div>
        )}

        <div className="spacer" />

        <button className="primary" onClick={confirm}
                disabled={busy || !meta || !curationConfirmable(job?.state, dirty)
                  || apBad || !gridStat.ok
                  || !!toolBlock}
                style={{ width: "100%", padding: "10px", fontSize: 14, marginTop: 8 }}>
          {busy ? "Generating…" : "Confirm alignment"}
        </button>

        {err && <div className="result" style={{ color: "#f85149" }}>{err}</div>}
      </div>

      <div className="canvas-wrap">
        {meta ? (
          <canvas
            ref={canvasRef}
            className="editor"
            onPointerDown={onPointerDown}
            onPointerLeave={() => {
              if (hoverRegionRef.current) {
                hoverRegionRef.current = null;
                scheduleDraw();
              }
            }}
            onWheel={onWheel}
            onContextMenu={(e) => e.preventDefault()}
          />
        ) : (
          <div className="muted">{err || "Loading bundle…"}</div>
        )}
      </div>
    </>
  );
}
