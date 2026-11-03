// Whole-lattice presets: arc and bulge. See ../../presets.js for the fields.
//
// Unlike the other four tools this one has no pointer gesture -- it is driven
// entirely from its panel. Every hook is optional, so a tool may be panel-only;
// what it still must do is the shared contract: end by writing ed.grid().dst and
// revalidating.
//
// The slider edits from a BASELINE captured when the gesture starts, so moving
// it back undoes it exactly and it composes with whatever was already on the
// lattice instead of replacing it.
import { validateGrid } from "../../warp.js";
import { presetGrid, presetAmount, PRESETS } from "../../presets.js";

export default {
  id: "preset",
  label: "Preset shape",
  initState: () => ({ which: "arc", frac: 0, base: null }),

  blurb: "Bow or swell the whole section at once, as a starting point before "
       + "local correction.",

  badges(ed) {
    const f = ed.st.frac || 0;
    return f ? [{ key: "preset",
                  text: `${ed.st.which} ${(f * 100).toFixed(0)}%` }] : [];
  },

  // A preset is one continuous quantity like the elastic push, so "too far" has
  // a defined answer: keep the largest fraction that still validates rather than
  // dropping a gesture the user mostly meant.
  apply(ed, which, frac) {
    const gr = ed.grid();
    if (!gr) return;
    const base = ed.st.base || gr.dst.map((p) => [p[0], p[1]]);
    const build = (f) => presetGrid(
      gr.src, base, which,
      presetAmount(gr.extent, f, gr.rows, gr.cols, gr.dispFrac),
      gr.extent, gr.rows, gr.cols, gr.dispFrac);

    let use = frac;
    let dst = build(use);
    if (!validateGrid(gr.src, dst, gr.R, gr.extent, gr.rows, gr.cols,
                      gr.dispFrac).ok) {
      let lo = 0, hi = frac;
      for (let k = 0; k < 12; k++) {
        const mid = (lo + hi) / 2;
        if (validateGrid(gr.src, build(mid), gr.R, gr.extent, gr.rows, gr.cols,
                         gr.dispFrac).ok) lo = mid; else hi = mid;
      }
      use = lo;
      dst = build(use);
      ed.setWarpMsg(Math.abs(use) < 0.01
        ? "That shape folds the tissue immediately here."
        : `Limited to ${(use * 100).toFixed(0)}% — more would fold the tissue.`);
    } else {
      ed.setWarpMsg(null);
    }
    gr.dst = dst;
    ed.setSt({ which, frac: use, base });
    ed.revalidate();
    ed.scheduleDraw();
  },

  // The baseline is taken once, at the start of a slider gesture, and released
  // on commit so the next gesture starts from the result of this one.
  begin(ed) {
    if (ed.st.base) return;
    const gr = ed.grid();
    if (!gr) return;
    ed.pushHistory();
    ed.setSt({ base: gr.dst.map((p) => [p[0], p[1]]) });
  },

  commit(ed) {
    ed.setSt({ base: null, frac: 0 });
  },

  snapshot: (ed) => ({ ...ed.st }),
  restore: (ed, snap) => ed.setSt(snap || { which: "arc", frac: 0, base: null }),

  PRESETS,
};
