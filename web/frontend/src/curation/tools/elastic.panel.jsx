// Sidebar controls for the "elastic" tool. Kept apart from elastic.js so the tool
// LOGIC stays importable by `node` for tests -- JSX is not.
import React from "react";
import { defaultEps, NU_MIN, NU_MAX } from "../../elastic.js";

export default function Panel({ ed }) {
    const gr = ed.grid();
    const ext = gr?.extent;
    const eps = ed.st.eps || (ext ? defaultEps(ext) : 0);
    return (
      <>
        <h2>Push reach</h2>
        <input type="range" min="5" max="60" step="1"
               value={ext ? Math.round((100 * eps) / (ext[1] - ext[0])) : 25}
               onChange={(e) => ext && ed.setSt({
                 eps: (Number(e.target.value) / 100) * (ext[1] - ext[0]) })}
               style={{ width: "100%" }} />
        <div className="muted" style={{ fontSize: 12, lineHeight: 1.45 }}>
          How far the deformation carries beyond the region you grab.
          {ext ? ` Currently ${eps.toFixed(0)} um.` : ""}
        </div>

        <h2 style={{ marginTop: 14 }}>Neighbour response</h2>
        <input type="range" min={NU_MIN} max={NU_MAX} step="0.01" value={ed.st.nu}
               onChange={(e) => ed.setSt({ nu: Number(e.target.value) })}
               style={{ width: "100%" }} />
        <div className="muted" style={{ fontSize: 12, lineHeight: 1.45 }}>
          {ed.st.nu >= 0.35
            ? "Incompressible: neighbours are squeezed around the region."
            : ed.st.nu >= 0.18
            ? "Mixed: neighbours partly compress, partly flow around."
            : "Compressible: neighbours are dragged along and compressed."}
          {" "}(nu {Number(ed.st.nu).toFixed(2)})
        </div>

        <div className="result" style={{
          marginTop: 10, padding: "12px 14px", borderColor: "#ffd23f",
          background: "rgba(255,210,63,0.07)",
        }}>
          <div style={{ fontWeight: 700, color: "#ffd23f" }}>
            {ed.st.grabbed ? `Pushing ${ed.st.grabbed}` : "Grab a region and drag"}
          </div>
          <div className="muted" style={{ marginTop: 6, lineHeight: 1.45 }}>
            The region you grab moves as a block; surrounding tissue deforms
            elastically around it. Corners of the section stay pinned. A push that
            would fold the tissue is trimmed back to the furthest it can legally
            go rather than discarded.
          </div>
        </div>
      </>
    );
}
