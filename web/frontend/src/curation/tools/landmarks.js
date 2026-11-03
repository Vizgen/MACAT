// Landmark pairs: click an atlas point, then the tissue point it belongs on.
// Three or more pairs are fitted into the shared control lattice.
//
// This tool was fully implemented and unit-tested (landmarksToGrid, covered by
// tests/test_warp_ui.mjs) but had no button for months -- it was unreachable
// because the old UI hard-coded which tools existed. Listing it in TOOLS is now
// the whole of "enabling" it.
import {
  inverseWarpPoint, landmarksToGrid, warpWeights, warpPoints,
} from "../../warp.js";

const DOT_PX = 5.0;
const MIN_PAIRS = 3;

export default {
  id: "landmarks",
  label: "Landmark pairs",
  initState: () => ({ pairs: [], pending: null }),

  // The mid-gesture "now click the tissue" cue is NOT lost with the live hint
  // line: landmarks.panel.jsx already says "Step 2 of 2 - place DAPI target N"
  // and recolours itself, which is a louder version of the same thing.
  blurb: "Click an atlas landmark, then its matching tissue location. "
       + "Add 3-8 well-spaced pairs.",

  cursor: () => "crosshair",

  onDown(ed, evt, sx, sy) {
    const gr = ed.grid();
    const clicked = ed.toUm(sx, sy);
    if (!ed.st.pending) {
      ed.setSt({ pending: { atlas: inverseWarpPoint(gr.src, gr.dst, gr.R, clicked) } });
      ed.setWarpMsg("Atlas point selected. Now click the matching point on the tissue.");
      ed.setCursor("crosshair");
      ed.scheduleDraw();
      return true;
    }
    ed.pushHistory();
    const next = [...ed.st.pairs,
                  { atlas: [...ed.st.pending.atlas], tissue: clicked }];
    const oldDst = ed.copyNodes(gr.dst);
    gr.dst = landmarksToGrid(gr.src, next, gr.R, gr.extent, gr.rows, gr.cols);
    const verdict = ed.revalidate();
    if (!verdict.ok) {
      gr.dst = oldDst;
      ed.revalidate();
      ed.popHistory();
      ed.setWarpMsg(`Pair rejected — ${verdict.reason}. Choose a closer tissue point.`);
    } else {
      ed.setSt({ pairs: next, pending: null });
      ed.setWarpMsg(null);
    }
    ed.scheduleDraw();
    return true;
  },

  // Undo after a COMPLETED pair removes the whole pair rather than returning to
  // the intermediate "waiting for the tissue point" state.
  snapshot: (ed) => ({
    pairs: ed.st.pairs.map((p) => ({ atlas: [...p.atlas], tissue: [...p.tissue] })),
    pending: null,
  }),
  restore: (ed, snap) => ed.setSt({ pairs: snap.pairs, pending: snap.pending }),

  interceptUndo(ed) {
    if (!ed.st.pending) return false;
    ed.setSt({ pending: null });
    ed.setWarpMsg(null);
    ed.scheduleDraw();
    return true;
  },

  // The atlas endpoint follows the current local warp; the tissue endpoint stays
  // fixed on the background image.
  draw(ed, ctx) {
    ctx.save();
    ctx.lineWidth = 1.5;
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ed.st.pairs.forEach((pair, i) => {
      const a = ed.toScreen(...ed.warpOnly(pair.atlas));
      const t = ed.toScreen(...pair.tissue);
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]); ctx.lineTo(t[0], t[1]);
      ctx.strokeStyle = "rgba(255,210,63,0.85)"; ctx.stroke();
      for (const [pt, fill] of [[a, "#ffd23f"], [t, "#40e0ff"]]) {
        ctx.beginPath(); ctx.arc(pt[0], pt[1], DOT_PX, 0, 7);
        ctx.fillStyle = fill; ctx.fill();
        ctx.strokeStyle = "rgba(4,8,14,0.95)"; ctx.lineWidth = 1.5; ctx.stroke();
      }
      ctx.fillStyle = "#071018";
      ctx.fillText(String(i + 1), a[0], a[1]);
      ctx.fillText(String(i + 1), t[0], t[1]);
    });
    if (ed.st.pending) {
      const a = ed.toScreen(...ed.warpOnly(ed.st.pending.atlas));
      ctx.beginPath(); ctx.arc(a[0], a[1], DOT_PX + 2.5, 0, 7);
      ctx.fillStyle = "#ffd23f"; ctx.fill();
      ctx.strokeStyle = "#071018"; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.beginPath(); ctx.arc(a[0], a[1], DOT_PX + 7, 0, 7);
      ctx.strokeStyle = "rgba(255,210,63,0.95)"; ctx.lineWidth = 2; ctx.stroke();
      ctx.fillStyle = "#071018";
      ctx.fillText(String(ed.st.pairs.length + 1), a[0], a[1]);
    }
    ctx.restore();
  },

  dirty: (ed) => ed.st.pairs.length > 0 || !!ed.st.pending,

  // Residual of the fitted lattice at the pairs the user placed. This is the
  // tool's own accuracy readout, so it lives with the tool rather than in the
  // host's badge row.
  badges(ed) {
    const out = [{ key: "pairs", text: `${ed.st.pairs.length} point pairs` }];
    const g = ed.grid();
    if (!g || !ed.st.pairs.length) return out;
    const w = warpWeights(g.fac, g.dst);
    const mapped = warpPoints(g.fac, w, ed.st.pairs.map((p) => p.atlas));
    const err = mapped.map((p, i) => Math.hypot(
      p[0] - ed.st.pairs[i].tissue[0], p[1] - ed.st.pairs[i].tissue[1]));
    const rms = Math.sqrt(err.reduce((a, e) => a + e * e, 0) / err.length);
    out.push({ key: "rms", text: `RMS ${rms.toFixed(0)} um` });
    out.push({ key: "max", text: `max error ${Math.max(...err).toFixed(0)} um` });
    return out;
  },

  // A half-finished pair, or too few to fit, is a warp the user did not mean.
  blocks: (ed) => {
    if (ed.st.pending) return "Complete the current pair by clicking its tissue location.";
    if (ed.st.pairs.length > 0 && ed.st.pairs.length < MIN_PAIRS)
      return `Add at least ${MIN_PAIRS} point pairs, or Reset local to use affine only.`;
    return null;
  },

};
