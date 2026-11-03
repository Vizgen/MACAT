// Sidebar controls for the "direct" tool. Kept apart from direct.js so the tool
// LOGIC stays importable by `node` for tests -- JSX is not.
import React from "react";

export default function Panel({ ed }) {
    const armed = !!ed.st.anchor;
    return (
      <div className="result" style={{
        marginTop: 10, padding: "12px 14px", borderColor: "#75e9ff",
        background: "rgba(64,224,255,0.07)",
      }}>
        <div style={{ fontWeight: 700, color: "#75e9ff" }}>
          {armed ? "Now click the target position" : "Drag the line itself"}
        </div>
        <div className="muted" style={{ marginTop: 6, lineHeight: 1.45 }}>
          {armed
            ? "The atlas point is selected. Click the matching DAPI edge to complete this pull."
            : "Grab a white or blue atlas boundary, then pull it onto the matching DAPI edge. If drag is unavailable, click the line once and then click its target."}
        </div>
        <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
          Work from large landmarks to small ones. Use Undo if a pull is not helpful.
        </div>
      </div>
    );
}
