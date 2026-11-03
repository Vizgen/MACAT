// Sidebar controls for the "preset" tool. Logic lives in preset.js so node can
// exercise it; this file is only the controls.
import React from "react";
import tool from "./preset.js";
import { PRESETS } from "../../presets.js";

export default function Panel({ ed }) {
  const which = ed.st.which || "arc";
  const frac = ed.st.frac || 0;
  const hint = (PRESETS.find((p) => p.id === which) || {}).hint || "";
  return (
    <>
      <div className="seg" style={{ width: "100%" }}>
        {PRESETS.map((p) => (
          <button key={p.id} className={which === p.id ? "active" : ""}
                  onClick={() => { tool.begin(ed); tool.apply(ed, p.id, frac); }}>
            {p.label}
          </button>
        ))}
      </div>
      <input type="range" min={-1} max={1} step={0.02} value={frac}
             onPointerDown={() => tool.begin(ed)}
             onChange={(e) => {
               tool.begin(ed);
               tool.apply(ed, which, Number(e.target.value));
             }}
             onPointerUp={() => tool.commit(ed)}
             style={{ width: "100%", marginTop: 8 }} />
      <div className="muted" style={{ fontSize: 12, lineHeight: 1.45 }}>
        {hint}
        <br />
        {frac === 0
          ? "Drag the slider; either direction."
          : `${(frac * 100).toFixed(0)}% of the per-node budget. Release to keep `
            + `it, or drag back to 0 to undo it exactly.`}
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>
        Applies to the whole lattice at once and leaves the frame alone, so the
        tissue outline does not move. Use it before local correction, not after.
      </div>
    </>
  );
}
