// Direct drag: grab an atlas boundary and pull it onto the matching DAPI edge.
// A Gaussian-weighted pull applied to the shared control lattice.
import { directDragGrid, inverseWarpPoint } from "../../warp.js";

const RADIUS_FRAC = 0.22;   // pull radius as a fraction of the smaller extent axis

export default {
  id: "direct",
  label: "Direct drag",
  initState: () => ({ anchor: null }),   // {anchor, downUm, radius} between clicks

  blurb: "Grab any atlas boundary and drag it directly onto the matching "
       + "DAPI feature.",

  cursor: () => "grab",

  onDown(ed, evt, sx, sy) {
    const gr = ed.grid();
    const clicked = ed.toUm(sx, sy);
    // Two-click fallback for embedded browsers that swallow drag moves: the
    // first click stores the anchor, the next supplies the target.
    const pending = ed.st.anchor;
    if (pending) {
      ed.pushHistory();
      gr.dst = directDragGrid(
        gr.src, gr.dst, pending.anchor,
        [clicked[0] - pending.downUm[0], clicked[1] - pending.downUm[1]],
        pending.radius, gr.extent, gr.rows, gr.cols,
      );
      const verdict = ed.revalidate();
      if (!verdict.ok) {
        ed.undo();
        ed.setWarpMsg(`Pull rejected — ${verdict.reason}. Choose a closer target.`);
      } else {
        ed.setSt({ anchor: null });
        ed.setWarpMsg(null);
      }
      ed.scheduleDraw();
      return true;
    }
    ed.pushHistory();
    ed.dragRef.current = {
      type: "brush",
      downUm: clicked,
      // weight the lattice around the point currently VISIBLE under the pointer,
      // so repeated pulls compose instead of fighting each other
      anchor: inverseWarpPoint(gr.src, gr.dst, gr.R, clicked),
      dst0: ed.copyNodes(gr.dst),
      radius: Math.min(gr.extent[1] - gr.extent[0],
                       gr.extent[3] - gr.extent[2]) * RADIUS_FRAC,
    };
    ed.setCursor("grabbing");
    ed.setWarpMsg(null);
    return true;
  },

  onMove(ed, evt) {
    const drag = ed.dragRef.current;
    if (!drag || drag.type !== "brush") return false;
    const gr = ed.grid();
    const um = ed.evtUm(evt);
    gr.dst = directDragGrid(
      gr.src, drag.dst0, drag.anchor,
      [um[0] - drag.downUm[0], um[1] - drag.downUm[1]],
      drag.radius, gr.extent, gr.rows, gr.cols,
    );
    ed.revalidate();
    ed.scheduleDraw();
    return true;
  },

  onUp(ed, evt) {
    const drag = ed.dragRef.current;
    if (!drag || drag.type !== "brush") return false;
    ed.dragRef.current = null;
    const gr = ed.grid();
    // Embedded browsers may report only the release position; apply that final
    // delta here even when no move event ever arrived.
    if (evt && Number.isFinite(evt.clientX)) {
      const um = ed.evtUm(evt);
      gr.dst = directDragGrid(
        gr.src, drag.dst0, drag.anchor,
        [um[0] - drag.downUm[0], um[1] - drag.downUm[1]],
        drag.radius, gr.extent, gr.rows, gr.cols,
      );
    }
    const v = ed.revalidate();
    if (!v.ok) {
      gr.dst = ed.copyNodes(drag.dst0);
      ed.revalidate();
      ed.popHistory();
      ed.setWarpMsg(`Rejected — ${v.reason}. The pull was undone.`);
    } else if (v.nMoved === 0) {
      // nothing moved: treat the press as the first half of a two-click pull
      ed.popHistory();
      ed.setSt({ anchor: { anchor: drag.anchor, downUm: drag.downUm,
                           radius: drag.radius } });
      ed.setWarpMsg("Atlas point selected. Now click where it should land.");
    }
    ed.setCursor("grab");
    ed.scheduleDraw();
    return true;
  },

  dirty: (ed) => !!ed.st.anchor,

  interceptUndo(ed) {
    if (!ed.st.anchor) return false;
    ed.setSt({ anchor: null });
    ed.setWarpMsg(null);
    ed.scheduleDraw();
    return true;
  },

};
