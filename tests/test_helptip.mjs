// Behaviour tests for the sidebar "?" tip placement (web/frontend/src/helptipPlacement.js).
//
// Run: node tests/test_helptip.mjs
//
// The maths lives in a plain .js module rather than inside CurationStep.jsx so it
// can be driven here; node cannot parse JSX, so the <HelpTip> half is
// presentation only and is not imported.
//
// What this pins down is the two ways the panel can end up unreadable: anchored
// near the right edge it would hang off the side, and anchored near the foot of a
// long sidebar it would hang off the bottom.
import assert from "node:assert/strict";
import {
  helpTipPosition, HELPTIP_W, HELPTIP_GAP, HELPTIP_MARGIN,
} from "../web/frontend/src/helptipPlacement.js";

let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log("  ok  " + name); };
const rect = (left, top, h = 14) => ({ left, top, bottom: top + h, height: h });
const VIEW = { width: 1600, height: 900 };

console.log("helptip.js");

test("opens just below the icon, left-aligned with it", () => {
  const p = helpTipPosition(rect(120, 300), VIEW);
  assert.equal(p.top, 300 + 14 + HELPTIP_GAP);
  assert.equal(p.left, 116);
});

test("the panel is allowed to overhang the 320px sidebar", () => {
  // the whole point of position:fixed -- 116 + 340 = 456, well past the sidebar
  const p = helpTipPosition(rect(120, 300), VIEW);
  assert.ok(p.left + HELPTIP_W > 320);
});

test("an icon near the right edge pulls the panel back on screen", () => {
  const p = helpTipPosition(rect(1550, 300), VIEW);
  assert.equal(p.left, VIEW.width - HELPTIP_W - HELPTIP_MARGIN);
  assert.ok(p.left + HELPTIP_W <= VIEW.width - HELPTIP_MARGIN);
});

test("the panel never starts off the left edge either", () => {
  const p = helpTipPosition(rect(0, 300), { width: 200, height: 900 });
  assert.equal(p.left, HELPTIP_MARGIN);
});

test("without a measured height it just goes below", () => {
  // first pass: the panel has not rendered yet, so no flip decision is possible
  const p = helpTipPosition(rect(120, 880), VIEW);
  assert.equal(p.top, 880 + 14 + HELPTIP_GAP);
});

test("a measured panel that would overflow the bottom flips above the icon", () => {
  const anchor = rect(120, 800);
  const p = helpTipPosition(anchor, VIEW, 160);
  assert.equal(p.top, anchor.top - 160 - HELPTIP_GAP);
  assert.ok(p.top + 160 < anchor.top, "must clear the icon it belongs to");
});

test("a measured panel that fits stays below", () => {
  const p = helpTipPosition(rect(120, 300), VIEW, 160);
  assert.equal(p.top, 300 + 14 + HELPTIP_GAP);
});

test("flipping is exact at the boundary, not approximate", () => {
  const anchor = rect(120, 500);
  const below = anchor.bottom + HELPTIP_GAP;
  const exact = VIEW.height - HELPTIP_MARGIN - below;      // tallest that still fits
  assert.equal(helpTipPosition(anchor, VIEW, exact).top, below);
  assert.notEqual(helpTipPosition(anchor, VIEW, exact + 1).top, below);
});

test("a window shorter than the panel pins to the top margin", () => {
  // nothing fits anywhere; keeping the FIRST lines on screen is the least bad
  const p = helpTipPosition(rect(120, 40), { width: 1600, height: 200 }, 400);
  assert.equal(p.top, HELPTIP_MARGIN);
});

console.log(`\n${passed} helptip checks passed`);
