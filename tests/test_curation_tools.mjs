// Behaviour tests for the correction tools (web/frontend/src/curation/tools/*.js).
//
// Run: node tests/test_curation_tools.mjs
//
// The tools are deliberately split so their LOGIC is plain JS with no React and
// no DOM: that is what lets this file drive a real gesture against a fake editor
// and check the lattice that comes out. The .panel.jsx halves are not imported
// here -- they are presentation and node cannot parse JSX.
//
// The contract every tool must honour, and what this file pins down:
//   1. a gesture ends with a lattice the shared validator accepts, and
//   2. a rejected gesture leaves the lattice exactly as it found it.
// Those two are what keep a new tool from being able to corrupt a curation.
//
// NOTE: `preset`, `elastic` and `landmarks` are in the list below although they
// are hashed out of the registry in index.js. That is deliberate -- the file is still on disk and still works,
// and the directory check compares this list against the DIRECTORY, not the
// registry. Removing it here would leave retained code untested.
import assert from "node:assert/strict";
import fs from "node:fs";
import direct from "../web/frontend/src/curation/tools/direct.js";
import elastic from "../web/frontend/src/curation/tools/elastic.js";
import landmarks from "../web/frontend/src/curation/tools/landmarks.js";
import grid from "../web/frontend/src/curation/tools/grid.js";
import preset from "../web/frontend/src/curation/tools/preset.js";
import {
  gridNodes, gridRadius, buildWarp, validateGrid, nodeKinds,
  NODE_CORNER, NODE_FREE,
} from "../web/frontend/src/warp.js";

const TOOLS = [direct, elastic, landmarks, grid, preset];
const extent = [-5825, 5825, -3425, 3425];
const ROWS = 7, COLS = 7;

function copyNodes(n) { return n.map((p) => [p[0], p[1]]); }

// A fake editor: the same surface CurationStep hands a tool, backed by plain
// objects so a gesture can be driven without a browser.
function makeEditor(tool) {
  const src = gridNodes(extent, ROWS, COLS);
  const R = gridRadius(extent, ROWS, COLS);
  const g = { src, R, fac: buildWarp(src, R), dst: copyNodes(src),
              extent, rows: ROWS, cols: COLS, dispFrac: null };
  const log = { history: [], msgs: [], draws: 0, cursor: null };
  const ed = {
    id: tool.id,
    st: tool.initState ? tool.initState() : {},
    setSt(patch) { ed.st = { ...ed.st, ...patch }; },
    grid: () => g,
    dragRef: { current: null },
    toUm: (sx, sy) => [sx, sy],
    evtUm: (e) => [e.clientX, e.clientY],
    toScreen: (lr, si) => [lr, si],
    warpOnly: (p) => p,
    matrix: () => null,
    copyNodes,
    hitNode: () => null,
    grabRegionAt: () => ({
      acronym: "TEST",
      rings: [{ role: "exterior",
                points: [[-2000, -1200], [2000, -1200], [2000, 1200],
                         [-2000, 1200], [-2000, -1200]] }],
    }),
    pushHistory() { log.history.push(copyNodes(g.dst)); },
    popHistory() { log.history.pop(); },
    undo() { const s = log.history.pop(); if (s) g.dst = s; },
    revalidate: () => validateGrid(g.src, g.dst, g.R, g.extent, g.rows, g.cols,
                                   g.dispFrac),
    scheduleDraw() { log.draws++; },
    setWarpMsg(m) { log.msgs.push(m); },
    setCursor(c) { log.cursor = c; },
    dims: { rows: ROWS, cols: COLS, restored: false },
    nMovable: ROWS * COLS - 4,
    useGridSize() {},
  };
  return { ed, g, log };
}

const nMoved = (g) => g.src.reduce(
  (n, p, i) => n + (Math.hypot(g.dst[i][0] - p[0], g.dst[i][1] - p[1]) > 1e-6 ? 1 : 0), 0);

// ---------------------------------------------------- registry-wide contract
{
  // This file lists the tools by hand, because index.js imports the .panel.jsx
  // halves and node cannot parse JSX. A hand-written list drifts: a new tool can
  // be shipped and simply never tested here, which is how `preset` nearly was.
  // Compare against the DIRECTORY instead, which is the one thing that cannot
  // disagree with what exists.
  const dir = new URL("../web/frontend/src/curation/tools/", import.meta.url);
  const onDisk = fs.readdirSync(dir)
    .filter((f) => f.endsWith(".js") && f !== "index.js")
    .map((f) => f.replace(/\.js$/, ""))
    .sort();
  assert.deepEqual(TOOLS.map((t) => t.id).sort(), onDisk,
                   `this file tests [${TOOLS.map((t) => t.id).sort()}] but the `
                   + `tools directory holds [${onDisk}] — add the missing one`);

  const ids = TOOLS.map((t) => t.id);
  assert.equal(new Set(ids).size, ids.length, "tool ids must be unique");
  for (const t of TOOLS) {
    assert.ok(t.id && t.label, "every tool needs an id and a label");
    const { ed } = makeEditor(t);
    // every tool must describe itself: the Correction tool help tip is built
    // from the registry, so a tool with no blurb would appear there unexplained
    assert.equal(typeof t.blurb, "string", `${t.id} needs a blurb`);
    assert.ok(t.blurb.length > 20, `${t.id}'s blurb says nothing useful`);
    if (t.cursor) assert.equal(typeof t.cursor(ed, 0, 0), "string");
    if (t.badges) assert.ok(Array.isArray(t.badges(ed)));
    if (t.blocks) {
      const b = t.blocks(ed);
      assert.ok(b === null || typeof b === "string");
    }
    if (t.initState) assert.equal(typeof t.initState(), "object");
  }
}
console.log("registry: ids unique, every declared hook has the right shape ok");

// ------------------------------------------------------------- elastic push
{
  const { ed, g, log } = makeEditor(elastic);
  elastic.onDown(ed, {}, 0, 0);
  assert.equal(ed.dragRef.current.type, "elastic");
  assert.equal(ed.st.grabbed, "TEST", "the grabbed region is reported to the UI");
  assert.equal(log.history.length, 1, "a gesture must be undoable");

  elastic.onMove(ed, { clientX: 0, clientY: 600 });
  assert.ok(nMoved(g) > 0, "the push must move lattice nodes");

  elastic.onUp(ed, { clientX: 0, clientY: 600 });
  assert.equal(ed.dragRef.current, null, "the drag must be released");
  assert.ok(ed.revalidate().ok, "a committed push must satisfy the validator");
  assert.equal(ed.st.grabbed, null);

  // corners are never moved by any tool -- the sampling box must keep its shape
  const kinds = nodeKinds(ROWS, COLS);
  for (let i = 0; i < g.src.length; i++) {
    if (kinds[i] !== NODE_CORNER) continue;
    assert.ok(Math.hypot(g.dst[i][0] - g.src[i][0], g.dst[i][1] - g.src[i][1]) < 1e-9,
              "corner moved");
  }
}
console.log("elastic: grab, push, release -> a valid lattice, corners pinned ok");

// an over-large push is TRIMMED, not discarded -- the tool's headline behaviour
{
  const { ed, g } = makeEditor(elastic);
  elastic.onDown(ed, {}, 0, 0);
  elastic.onUp(ed, { clientX: 0, clientY: 99999 });
  const v = ed.revalidate();
  assert.ok(v.ok, "an over-large push must be trimmed to something legal");
  assert.ok(nMoved(g) > 0, "and must not be thrown away entirely");
}
console.log("elastic: an over-large push is trimmed rather than discarded ok");

// ---------------------------------------------------------- landmark pairs
{
  const { ed, g } = makeEditor(landmarks);
  assert.equal(landmarks.blocks(ed), null, "no pairs yet is not a blocker");

  landmarks.onDown(ed, {}, 0, 0);                    // atlas point
  assert.ok(ed.st.pending, "first click arms the pair");
  assert.ok(landmarks.blocks(ed), "a half-finished pair must block Confirm");
  assert.ok(landmarks.interceptUndo(ed), "undo cancels the half-finished pair");
  assert.equal(ed.st.pending, null);

  landmarks.onDown(ed, {}, 0, 0);
  landmarks.onDown(ed, {}, 120, 90);                 // tissue point -> pair 1
  assert.equal(ed.st.pairs.length, 1);
  assert.ok(landmarks.blocks(ed), "one pair is below the 3-pair minimum");
  assert.ok(ed.revalidate().ok);
  assert.equal(landmarks.badges(ed)[0].text, "1 point pairs");

  const snap = landmarks.snapshot(ed);
  landmarks.restore(ed, { pairs: [], pending: null });
  assert.equal(ed.st.pairs.length, 0, "history restore clears the pairs");
  landmarks.restore(ed, snap);
  assert.equal(ed.st.pairs.length, 1, "and puts them back");
}
console.log("landmarks: pairing, gating, undo interception, history ok");

// ------------------------------------------------------------ direct drag
{
  const { ed, g, log } = makeEditor(direct);
  direct.onDown(ed, {}, 0, 0);
  assert.equal(ed.dragRef.current.type, "brush");
  direct.onMove(ed, { clientX: 300, clientY: 0 });
  assert.ok(nMoved(g) > 0);
  direct.onUp(ed, { clientX: 300, clientY: 0 });
  assert.ok(ed.revalidate().ok, "a committed pull must validate");
  assert.equal(ed.dragRef.current, null);
}
console.log("direct: press, pull, release -> a valid lattice ok");

// ------------------------------------------------------- grid node dragging
{
  const { ed, g } = makeEditor(grid);
  assert.equal(grid.showsGrid, true, "the grid tool is what reveals the lattice");
  assert.equal(grid.onDown(ed, {}, 0, 0), false,
               "a press that hits no node must fall through to panning");

  const kinds = nodeKinds(ROWS, COLS);
  const free = kinds.findIndex((k) => k === NODE_FREE);
  ed.hitNode = () => ({ idx: free, kind: NODE_FREE });
  assert.equal(grid.onDown(ed, {}, 0, 0), true);
  grid.onMove(ed, { clientX: 120, clientY: 80 });
  assert.equal(nMoved(g), 1, "exactly the dragged node moves");
  grid.onUp(ed);
  assert.ok(ed.revalidate().ok);
}
console.log("grid: node drag moves one node and validates ok");

// a gesture the validator refuses must leave the lattice untouched
{
  const { ed, g } = makeEditor(grid);
  const kinds = nodeKinds(ROWS, COLS);
  const free = kinds.findIndex((k) => k === NODE_FREE);
  ed.hitNode = () => ({ idx: free, kind: NODE_FREE });
  grid.onDown(ed, {}, 0, 0);
  const before = copyNodes(g.dst);
  // force a rejection the clamp cannot save it from
  g.dst[free] = [g.src[free][0] + 99999, g.src[free][1]];
  grid.onUp(ed);
  assert.deepEqual(g.dst, before, "a rejected gesture must roll all the way back");
  assert.ok(ed.revalidate().ok);
}
console.log("grid: a rejected gesture rolls back completely ok");

console.log("curation tool tests passed");
