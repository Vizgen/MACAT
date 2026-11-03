// Advanced grid: drag the control lattice directly. The lowest-level tool --
// everything the other tools do ends up expressed here.
import {
  GRID_ROWS, GRID_COLS, nodeDisplacementLimit, clampNodeDisplacement,
} from "../../warp.js";

export default {
  id: "grid",
  label: "Advanced grid",

  // No node count here on purpose: it changes with the density (165 at 13x13,
  // 25 at 7x7), and the GRID DENSITY panel directly below states the live figure.
  blurb: "Drag the movable grid nodes directly.",

  // The host draws the lattice itself (it owns the node hit-targets), so this
  // tool only has to say that the lattice should be visible at all.
  showsGrid: true,

  cursor: (ed, mx, my) => (ed.hitNode(mx, my) ? "grab" : "default"),

  onDown(ed, evt, sx, sy) {
    const nd = ed.hitNode(sx, sy);
    if (!nd) return false;                 // fall through to panning
    ed.pushHistory();
    ed.dragRef.current = {
      type: "node", idx: nd.idx, kind: nd.kind, downUm: ed.toUm(sx, sy),
      dst0: ed.copyNodes(ed.grid().dst),
    };
    ed.setCursor("grabbing");
    return true;
  },

  onMove(ed, evt) {
    const drag = ed.dragRef.current;
    if (!drag || drag.type !== "node") return false;
    const gr = ed.grid();
    const um = ed.evtUm(evt);
    const want = [drag.dst0[drag.idx][0] + (um[0] - drag.downUm[0]),
                  drag.dst0[drag.idx][1] + (um[1] - drag.downUm[1])];
    // Clamp to the validator's exact budget so an imprecise pointer stops at the
    // legal boundary instead of being rejected on release.
    const limit = nodeDisplacementLimit(gr.extent, drag.kind, gr.rows, gr.cols,
                                        gr.dispFrac);
    gr.dst[drag.idx] = clampNodeDisplacement(drag.kind, gr.src[drag.idx], want, limit);
    ed.revalidate();
    ed.scheduleDraw();
    return true;
  },

  onUp(ed) {
    const drag = ed.dragRef.current;
    if (!drag || drag.type !== "node") return false;
    ed.dragRef.current = null;
    const gr = ed.grid();
    const v = ed.revalidate();
    if (!v.ok) {
      // never leave the editor holding a grid the backend would reject
      gr.dst = ed.copyNodes(drag.dst0);
      ed.revalidate();
      ed.popHistory();
      ed.setWarpMsg(`Rejected — ${v.reason}. The node was moved back.`);
    }
    ed.setCursor("default");
    ed.scheduleDraw();
    return true;
  },

  badges: (ed) => [{ key: "dims", text: `${ed.dims.rows}x${ed.dims.cols} grid` }],

};
