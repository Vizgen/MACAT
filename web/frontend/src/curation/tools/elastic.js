// Elastic push: grab a region, drag it as a block, and let the surrounding
// tissue deform around it. Regularized Kelvinlets; see ../../elastic.js.
import { inverseWarpPoint, validateGrid, MAX_DISP_FRAC_CEILING } from "../../warp.js";
import {
  elasticPushGrid, defaultEps, DEFAULT_NU, NU_MIN, NU_MAX,
} from "../../elastic.js";

const BISECT_STEPS = 12;

// One push into the lattice, at a fraction of the requested delta.
function build(ed, drag, delta) {
  const gr = ed.grid();
  return elasticPushGrid(
    gr.src, drag.dst0, drag.rings, drag.anchor, delta,
    ed.st.eps || defaultEps(gr.extent), ed.st.nu, gr.extent, gr.rows, gr.cols,
    MAX_DISP_FRAC_CEILING, gr.R, gr.fac,
  );
}

export default {
  id: "elastic",
  label: "Elastic push",
  initState: () => ({ eps: null, nu: DEFAULT_NU, grabbed: null }),

  blurb: "Click inside a region and drag: it moves as a block while "
       + "neighbouring tissue is pushed aside.",

  cursor: () => "grab",

  onDown(ed, evt, sx, sy) {
    const gr = ed.grid();
    const clicked = ed.toUm(sx, sy);
    const region = ed.grabRegionAt(sx, sy);
    if (!region) {
      ed.setWarpMsg("Click inside a region to grab it.");
      return true;
    }
    ed.pushHistory();
    ed.dragRef.current = {
      type: "elastic",
      downUm: clicked,
      // un-warp the click so repeated pushes compose in lattice space
      anchor: inverseWarpPoint(gr.src, gr.dst, gr.R, clicked),
      rings: region.rings,
      acronym: region.acronym,
      dst0: ed.copyNodes(gr.dst),
    };
    // This tool authors on the raised budget, so the grid is validated against
    // it from here on -- including after a reload, since buildGrid restores it.
    gr.dispFrac = MAX_DISP_FRAC_CEILING;
    ed.setSt({ grabbed: region.acronym });
    ed.setCursor("grabbing");
    ed.setWarpMsg(null);
    return true;
  },

  onMove(ed, evt) {
    const drag = ed.dragRef.current;
    if (!drag || drag.type !== "elastic") return false;
    const um = ed.evtUm(evt);
    ed.grid().dst = build(ed, drag,
      [um[0] - drag.downUm[0], um[1] - drag.downUm[1]]);
    ed.revalidate();
    ed.scheduleDraw();
    return true;
  },

  onUp(ed, evt) {
    const drag = ed.dragRef.current;
    if (!drag || drag.type !== "elastic") return false;
    ed.dragRef.current = null;
    const gr = ed.grid();
    const um = (evt && Number.isFinite(evt.clientX)) ? ed.evtUm(evt) : null;
    const want = um ? [um[0] - drag.downUm[0], um[1] - drag.downUm[1]] : null;
    if (want) gr.dst = build(ed, drag, want);

    let v = ed.revalidate();
    if (!v.ok && want) {
      // Deliberately NOT the rollback the node and direct tools use. An elastic
      // push is one continuous quantity, so "too far" has a well-defined answer:
      // bisect for the largest magnitude that still validates and keep it,
      // rather than discarding a gesture the user mostly meant. The field is
      // rebuilt at each trial, so this is exact rather than extrapolated.
      let lo = 0, hi = 1;
      for (let k = 0; k < BISECT_STEPS; k++) {
        const mid = (lo + hi) / 2;
        const trial = build(ed, drag, [want[0] * mid, want[1] * mid]);
        if (validateGrid(gr.src, trial, gr.R, gr.extent, gr.rows, gr.cols,
                         gr.dispFrac).ok) lo = mid; else hi = mid;
      }
      gr.dst = build(ed, drag, [want[0] * lo, want[1] * lo]);
      v = ed.revalidate();
      const reached = Math.hypot(want[0], want[1]) * lo;
      if (v.ok) {
        ed.setWarpMsg(lo > 0.01
          ? `Push limited to ${reached.toFixed(0)} um — going further folds the tissue.`
          : "That push folds the tissue immediately. Try a smaller region or a coarser grid.");
      }
    }
    if (!v.ok) {
      gr.dst = ed.copyNodes(drag.dst0);
      ed.revalidate();
      ed.popHistory();
      ed.setWarpMsg(`Rejected — ${v.reason}. The push was undone.`);
    }
    ed.setSt({ grabbed: null });
    ed.setCursor("grab");
    ed.scheduleDraw();
    return true;
  },

  // The region currently held. Drawn from the SAME rings the field measures
  // distance to, pushed through the warp only (they are already post-affine),
  // so what is highlighted is exactly what is rigid.
  draw(ed, ctx) {
    const drag = ed.dragRef.current;
    if (!drag || drag.type !== "elastic") return;
    ctx.save();
    ctx.lineWidth = 2.6;
    ctx.strokeStyle = "#ffd23f";
    ctx.setLineDash([]);
    for (const ring of drag.rings) {
      const pts = (ring.points || []).map((pt) => ed.toScreen(...ed.warpOnly(pt)));
      if (pts.length < 2) continue;
      ctx.beginPath();
      pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.closePath();
      ctx.stroke();
    }
    ctx.restore();
  },

};
