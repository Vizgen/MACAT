// Sidebar controls for the "grid" tool. Kept apart from grid.js so the tool
// LOGIC stays importable by `node` for tests -- JSX is not.
import React from "react";
import { GRID_ROWS, GRID_COLS } from "../../warp.js";

export default function Panel({ ed }) {
    return (
      <>
        <h2>Grid density</h2>
        <div className="result" style={{ marginTop: 8, fontSize: 13 }}>
          {ed.dims.rows}x{ed.dims.cols} grid - {ed.nMovable} movable nodes
        </div>
        <div className="muted" style={{ marginTop: 8, fontSize: 12, lineHeight: 1.45 }}>
          The fine grid provides local control for irregular tissue. Corners stay
          pinned; edge nodes slide only along their own edge.
        </div>
        {ed.dims.restored
         && !(ed.dims.rows === GRID_ROWS && ed.dims.cols === GRID_COLS) && (
          <div className="result" style={{ marginTop: 8, fontSize: 12 }}>
            This older job keeps its saved {ed.dims.rows}x{ed.dims.cols} grid.
            <button style={{ marginTop: 8, width: "100%" }}
                    onClick={() => ed.useGridSize(GRID_ROWS, GRID_COLS)}>
              Switch to {GRID_ROWS}x{GRID_COLS} (reset local warp)
            </button>
          </div>
        )}
      </>
    );
}
