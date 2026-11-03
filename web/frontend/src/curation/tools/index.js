// The correction-tool registry. THIS LIST IS THE ONLY REGISTRATION POINT:
// adding a tool means adding its two files and one line below; removing one
// means deleting both. Nothing else in the application names a tool.
//
// Each tool is split in two on purpose:
//   <id>.js          behaviour -- plain JS, so `node tests/test_curation_tools.mjs`
//                    can exercise it headlessly, the same discipline warp.js and
//                    elastic.js follow
//   <id>.panel.jsx   the sidebar controls
// This file is what re-attaches them.
//
// Order is the order of the buttons in the sidebar.
import direct from "./direct.js";
import grid from "./grid.js";
import DirectPanel from "./direct.panel.jsx";
import GridPanel from "./grid.panel.jsx";

export const TOOLS = [
  { ...direct, Panel: DirectPanel },
  { ...grid, Panel: GridPanel },
  // Elastic push and landmark pairs are hashed out as of 2026-11-03: users did
  // not find them as intuitive as direct drag and the grid. Both stay on disk
  // and tested; restoring them is these imports and lines, in this order
  // between direct and grid:
  //
  //   import elastic from "./elastic.js";
  //   import landmarks from "./landmarks.js";
  //   import ElasticPanel from "./elastic.panel.jsx";
  //   import LandmarksPanel from "./landmarks.panel.jsx";
  //   { ...elastic, Panel: ElasticPanel },
  //   { ...landmarks, Panel: LandmarksPanel },
  //
  // Arc and bulge are hashed out as of 2026-09-29: they work, they are tested,
  // and they were not useful on real sections -- a whole-lattice bow or swell
  // is not the correction these slides actually need. The files stay, so
  // restoring them is the two imports and the one line below:
  //
  //   import preset from "./preset.js";
  //   import PresetPanel from "./preset.panel.jsx";
  //   { ...preset, Panel: PresetPanel },
];
export const DEFAULT_TOOL = direct.id;

export function getTool(id) {
  return TOOLS.find((t) => t.id === id) || TOOLS[0];
}

export function initialToolState() {
  return Object.fromEntries(
    TOOLS.map((t) => [t.id, t.initState ? t.initState() : {}]));
}
