// Sidebar controls for the "landmarks" tool. Kept apart from landmarks.js so the tool
// LOGIC stays importable by `node` for tests -- JSX is not.
import React from "react";

export default function Panel({ ed }) {
    const pending = ed.st.pending;
    const n = ed.st.pairs.length;
    return (
      <div className="result" style={{
        marginTop: 10, padding: "12px 14px",
        borderColor: pending ? "#40e0ff" : "#ffd23f",
        background: pending ? "rgba(64,224,255,0.08)" : "rgba(255,210,63,0.07)",
      }}>
        <div style={{ fontWeight: 700, color: pending ? "#75e9ff" : "#ffe17a" }}>
          {pending
            ? `Step 2 of 2 — place DAPI target ${n + 1}`
            : `Step 1 of 2 — choose atlas point ${n + 1}`}
        </div>
        <div className="muted" style={{ marginTop: 6, lineHeight: 1.45 }}>
          {pending
            ? "Click the same anatomical feature where it should land on the DAPI tissue."
            : "Click a recognizable point on a white or blue atlas boundary."}
        </div>
        <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
          Atlas points are yellow, tissue targets blue. Spread them out: three
          well-separated pairs beat six clustered ones.
        </div>
      </div>
    );
}
