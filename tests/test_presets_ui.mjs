// Whole-lattice presets: arc and bulge (web/frontend/src/presets.js).
//
// Run: node tests/test_presets_ui.mjs
//
// A preset writes the shared control lattice like every other tool, so the
// things that must hold are the same things a drag has to honour -- and two
// more that are specific to editing the WHOLE lattice at once:
//
//   1. the FRAME must not move. Corners are pinned and edge nodes may only
//      slide along their own edge; a preset that asked them to move elsewhere
//      would be silently clamped into a shape nobody designed, and would drag
//      the tissue outline off the background image.
//   2. the two presets must mean the SAME thing at the same slider position,
//      or the control lies about its own strength.
//
// NOTE: arc and bulge are currently HASHED OUT of the tool registry -- they were
// not useful on real sections. The code is retained and these tests are retained
// with it, so that restoring the two lines in curation/tools/index.js is safe
// rather than a leap. Delete this file only if the preset files go too.
import assert from "node:assert/strict";
import { PRESETS, presetGrid, presetAmount } from "../web/frontend/src/presets.js";
import {
  gridNodes, gridRadius, validateGrid, nodeKinds, maxDisp,
  NODE_CORNER, NODE_EDGE_V, NODE_EDGE_H,
} from "../web/frontend/src/warp.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };

const extent = [-6550, 6550, -4425, 4425];
const ROWS = 13, COLS = 13;
const src = gridNodes(extent, ROWS, COLS);
const R = gridRadius(extent, ROWS, COLS);
const kinds = nodeKinds(ROWS, COLS);
const identity = () => src.map((p) => [p[0], p[1]]);
const moved = (dst, i) => Math.hypot(dst[i][0] - src[i][0], dst[i][1] - src[i][1]);

test("both presets are offered and named", () => {
  assert.deepEqual(PRESETS.map((p) => p.id), ["arc", "bulge"]);
  for (const p of PRESETS) assert.ok(p.hint.length > 20, `${p.id} has no hint`);
});

test("zero amount is exactly the identity, not nearly", () => {
  for (const p of PRESETS) {
    const dst = presetGrid(src, identity(), p.id, 0, extent, ROWS, COLS);
    for (let i = 0; i < src.length; i++) assert.equal(moved(dst, i), 0);
  }
});

test("an unknown preset is a no-op rather than a crash", () => {
  const dst = presetGrid(src, identity(), "twist", 500, extent, ROWS, COLS);
  for (let i = 0; i < src.length; i++) assert.equal(moved(dst, i), 0);
});

test("the frame never moves, at full strength, either direction", () => {
  for (const p of PRESETS) {
    for (const sign of [1, -1]) {
      const amt = sign * presetAmount(extent, 1, ROWS, COLS);
      const dst = presetGrid(src, identity(), p.id, amt, extent, ROWS, COLS);
      for (let i = 0; i < src.length; i++) {
        if (kinds[i] === NODE_CORNER) {
          assert.equal(moved(dst, i), 0, `${p.id}: a corner moved`);
        }
        // an edge node may slide ALONG its edge, never off it
        if (kinds[i] === NODE_EDGE_V) {
          assert.ok(Math.abs(dst[i][0] - src[i][0]) < 1e-9,
                    `${p.id}: a top/bottom node moved horizontally`);
        }
        if (kinds[i] === NODE_EDGE_H) {
          assert.ok(Math.abs(dst[i][1] - src[i][1]) < 1e-9,
                    `${p.id}: a left/right node moved vertically`);
        }
      }
    }
  }
});

test("full strength still validates, and is not a no-op", () => {
  for (const p of PRESETS) {
    for (const sign of [1, -1]) {
      const amt = sign * presetAmount(extent, 1, ROWS, COLS);
      const dst = presetGrid(src, identity(), p.id, amt, extent, ROWS, COLS);
      const v = validateGrid(src, dst, R, extent, ROWS, COLS);
      assert.ok(v.ok, `${p.id} at ${sign > 0 ? "+" : "-"}100%: ${v.reason}`);
      assert.ok(v.nMoved > 20, `${p.id} barely moved anything (${v.nMoved})`);
    }
  }
});

test("the two presets mean the same strength at the same slider position", () => {
  // bulge's radial profile peaks well below 1, so without normalising it the
  // same slider would be far weaker for bulge than for arc
  const cap = maxDisp(extent, ROWS, COLS);
  for (const p of PRESETS) {
    const dst = presetGrid(src, identity(), p.id,
                           presetAmount(extent, 1, ROWS, COLS),
                           extent, ROWS, COLS);
    const peak = Math.max(...src.map((_, i) => moved(dst, i)));
    assert.ok(peak > cap * 0.9 && peak <= cap + 1e-6,
              `${p.id} peaks at ${peak.toFixed(0)}um against a ${cap.toFixed(0)}um `
              + `budget; the slider does not mean the same for both presets`);
  }
});

test("the slider is live across its whole travel", () => {
  // scaling the amount to the section instead of the cap made 85% of the
  // travel clamp to one lattice
  for (const p of PRESETS) {
    const peaks = [0.25, 0.5, 0.75, 1].map((f) => {
      const dst = presetGrid(src, identity(), p.id,
                             presetAmount(extent, f, ROWS, COLS),
                             extent, ROWS, COLS);
      return Math.max(...src.map((_, i) => moved(dst, i)));
    });
    for (let i = 1; i < peaks.length; i++) {
      assert.ok(peaks[i] > peaks[i - 1] * 1.1,
                `${p.id}: travel ${i} adds nothing (${peaks.join(", ")})`);
    }
  }
});

test("a preset edits from its baseline, so it composes and reverses", () => {
  const amt = presetAmount(extent, 0.5, ROWS, COLS);
  const once = presetGrid(src, identity(), "arc", amt, extent, ROWS, COLS);
  // same baseline, same amount -> same result, no accumulation
  const again = presetGrid(src, identity(), "arc", amt, extent, ROWS, COLS);
  for (let i = 0; i < src.length; i++) {
    assert.ok(Math.abs(once[i][1] - again[i][1]) < 1e-12);
  }
  // back to zero from the same baseline restores it exactly
  const back = presetGrid(src, identity(), "arc", 0, extent, ROWS, COLS);
  for (let i = 0; i < src.length; i++) assert.equal(moved(back, i), 0);
});

test("presets respect a coarser lattice", () => {
  for (const [r, c] of [[7, 7], [5, 5], [4, 4]]) {
    const s2 = gridNodes(extent, r, c), R2 = gridRadius(extent, r, c);
    for (const p of PRESETS) {
      const dst = presetGrid(s2, s2.map((q) => [q[0], q[1]]), p.id,
                             presetAmount(extent, 1, r, c), extent, r, c);
      const v = validateGrid(s2, dst, R2, extent, r, c);
      assert.ok(v.ok, `${p.id} at ${r}x${c}: ${v.reason}`);
    }
  }
});

console.log(`\n${passed} passed`);
