// Step 5's region overlay in the rank palette (web/frontend/src/regionColors.js).
//
// Run: node tests/test_region_colors.mjs
//
// "Rank colours" must mean the SAME colours as Results and the static figure,
// so the palette is pinned to its Python original, allen_alignment.figures.
// _leaf_result_color (values below computed from it). And the biggest region
// must get the first colour, every component of a region the same one.
import assert from "node:assert/strict";
import { rankColor, rankRecolor } from "../web/frontend/src/regionColors.js";

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };

test("the palette matches _leaf_result_color exactly", () => {
  // python: [figures._leaf_result_color(i) for i in range(8)]
  const py = ["#2E83D1", "#B9D14F", "#BE19D1", "#35F2A8", "#F28B5C", "#221DF2", "#5AD12E", "#D14F98"];
  py.forEach((c, i) => assert.equal(rankColor(i), c, `rank ${i}`));
});

const sq = (x0, y0, s) => [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]];

test("largest region first; components share a colour; outlines untouched", () => {
  const feats = [
    { kind: "region", acronym: "small", color: "#111", area_um2: 10 },
    { kind: "region", acronym: "big", color: "#222", area_um2: 60 },
    { kind: "region", acronym: "big", color: "#222", area_um2: 50 },   // 2nd component
    { kind: "region", acronym: "mid", color: "#333", area_um2: 100 },
    { kind: "outer", acronym: "tissue", color: "#dfeaf5" },
    { kind: "midline", acronym: "midline", color: "#7ee0d6" },
  ];
  const { features } = rankRecolor(feats);
  const col = (a) => features.find((f) => f.acronym === a).color;
  assert.equal(col("big"), rankColor(0), "110 total beats mid's 100");
  assert.equal(col("mid"), rankColor(1));
  assert.equal(col("small"), rankColor(2));
  assert.equal(features[1].color, features[2].color);
  assert.equal(col("tissue"), "#dfeaf5");
  assert.equal(col("midline"), "#7ee0d6");
  assert.equal(features[1].allen_color, "#222", "the Allen colour is kept");
  assert.equal(feats[1].color, "#222", "inputs are not modified");
});

test("landmark guides rank by their ring area, labels follow their region", () => {
  const feats = [
    { kind: "alignment_region", guide_family: "HPF", acronym: "HPF", color: "#0a0",
      rings: [{ role: "exterior", points: sq(0, 0, 10) }] },
    { kind: "alignment_region", guide_family: "TH", acronym: "TH", color: "#a00",
      rings: [{ role: "exterior", points: sq(0, 0, 20) }] },
  ];
  const { features } = rankRecolor(feats);
  assert.equal(features.find((f) => f.guide_family === "TH").color, rankColor(0));
  const { labels } = rankRecolor(
    [{ kind: "region", acronym: "CH", color: "#x", area_um2: 5 }],
    [{ acronym: "CH", short: "CH", color: "#x" }, { acronym: "zz", color: "#y" }]);
  assert.equal(labels[0].color, rankColor(0));
  assert.equal(labels[1].color, "#y", "a label with no region is left alone");
});

console.log(`${passed} passed`);
