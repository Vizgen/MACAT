// Unit tests for the elastic push field (web/frontend/src/elastic.js).
//
// Run: node tests/test_elastic_ui.mjs
//
// The properties worth pinning are the ones a reviewer would otherwise have to
// take on trust: that the grabbed region really is rigid, that the field really
// decays, that raising nu really does squeeze neighbours sideways instead of
// dragging them along, and that whatever the field asks for still lands inside
// the lattice budget the backend will re-check.
import assert from "node:assert/strict";
import {
  asRings, pointInRings, polygonDistance, kelvinletGrab, elasticField,
  elasticPushGrid, defaultEps, DEFAULT_NU, MAX_ANCHOR_GAIN,
} from "../web/frontend/src/elastic.js";
import {
  gridNodes, gridRadius, nodeKinds, buildWarp, validateGrid, forwardWarp,
  NODE_CORNER, MAX_DISP_FRAC_CEILING,
} from "../web/frontend/src/warp.js";

const extent = [-5825, 5825, -3425, 3425];
const square = [[-1000, -800], [1000, -800], [1000, 800], [-1000, 800],
                [-1000, -800]];

// ---------------------------------------------------------------- geometry
{
  assert.equal(asRings(square).length, 1, "a flat ring is accepted as one ring");
  assert.equal(asRings([{ role: "exterior", points: square }]).length, 1);

  assert.ok(pointInRings(square, [0, 0]), "centre is inside");
  assert.ok(!pointInRings(square, [5000, 0]), "far point is outside");

  assert.equal(polygonDistance(square, [0, 0]).d, 0, "distance is 0 inside");
  assert.equal(polygonDistance(square, [500, 300]).d, 0, "0 anywhere inside");

  const out = polygonDistance(square, [1500, 0]);
  assert.ok(Math.abs(out.d - 500) < 1e-6, `500 um to the right edge, got ${out.d}`);
  assert.ok(Math.abs(out.dir[0] - 1) < 1e-9 && Math.abs(out.dir[1]) < 1e-9,
            "direction points away from the outline");

  // a hole makes its interior count as outside
  const withHole = [{ role: "exterior", points: square },
                    { role: "hole", points: [[-200, -200], [200, -200],
                                             [200, 200], [-200, 200],
                                             [-200, -200]] }];
  assert.ok(!pointInRings(withHole, [0, 0]), "inside a hole is outside the region");
  assert.ok(polygonDistance(withHole, [0, 0]).d > 0, "and has a real distance");
}
console.log("geometry: ring handling, containment and distance ok");

// ------------------------------------------------------------------ kernel
{
  const delta = [300, -120];
  const at0 = kelvinletGrab([0, 0], delta, 2000, DEFAULT_NU);
  assert.ok(Math.abs(at0[0] - delta[0]) < 1e-9 && Math.abs(at0[1] - delta[1]) < 1e-9,
            `u(0) must equal delta exactly, got [${at0}]`);

  // normalisation holds across the whole nu range, i.e. (3/2 - b) never vanishes
  for (const nu of [0, 0.2, 0.45, 0.49]) {
    const u = kelvinletGrab([0, 0], delta, 1500, nu);
    assert.ok(Math.hypot(u[0] - delta[0], u[1] - delta[1]) < 1e-9,
              `u(0) === delta at nu=${nu}`);
  }

  // monotone decay with distance along the push direction
  const eps = 2000;
  let prev = Infinity;
  for (const d of [0, 500, 1000, 2000, 4000, 8000]) {
    const u = kelvinletGrab([d, 0], [100, 0], eps, DEFAULT_NU);
    const mag = Math.hypot(u[0], u[1]);
    assert.ok(mag <= prev + 1e-9, `magnitude must not grow at d=${d}`);
    prev = mag;
  }
  assert.ok(prev < 100 * 0.5, "far field is well below the drag magnitude");

  // eps is the reach dial: a larger radius moves a distant point further
  const near = kelvinletGrab([4000, 0], [100, 0], 1000, DEFAULT_NU);
  const wide = kelvinletGrab([4000, 0], [100, 0], 4000, DEFAULT_NU);
  assert.ok(Math.hypot(wide[0], wide[1]) > Math.hypot(near[0], near[1]),
            "a larger eps reaches further");
}
console.log("kernel: u(0)=delta, monotone decay, eps controls reach ok");

// --------------------------------------------------- the oil-droplet property
{
  // The lateral term is (b / r_e^3) (r . f) r, so it vanishes exactly where r is
  // perpendicular to the load -- beside the load there is nothing to deflect, by
  // symmetry. The squeeze shows up OFF-AXIS, where a neighbour is both ahead of
  // the push and to one side: it gets deflected sideways rather than merely
  // shoved along. Its weight is b = 1/(4(1-nu)), so it must grow with nu, which
  // is the difference between "neighbours get dragged along" (nu -> 0) and
  // "neighbours get squeezed around" (nu -> 0.5).
  const push = [400, 0];                        // load points +lr
  const diag = 2500 / Math.SQRT2;
  const probe = [diag, diag];                   // 45 degrees off the push
  const lateral = (nu) => kelvinletGrab(probe, push, 2500, nu)[1];  // si == sideways
  const low = lateral(0.0), high = lateral(0.49);
  assert.ok(high > low,
            `higher nu must squeeze more sideways (${low.toFixed(3)} -> ${high.toFixed(3)})`);
  assert.ok(low > 0, "there is a lateral response even at nu=0");
  assert.ok(high > low * 1.5, "and it is a pronounced difference, not a rounding");

  // directly beside the load, symmetry gives no sideways motion at any nu
  for (const nu of [0.0, 0.25, 0.49]) {
    assert.ok(Math.abs(kelvinletGrab([0, 2500], push, 2500, nu)[1]) < 1e-9,
              `no lateral deflection perpendicular to the load at nu=${nu}`);
  }
}
console.log("oil droplet: lateral response grows with nu ok");

// -------------------------------------------------------------- rigid core
{
  const delta = [700, -200];
  for (const p of [[0, 0], [900, 700], [-900, -700]]) {
    const u = elasticField(square, p, delta, 3000, DEFAULT_NU);
    assert.ok(Math.hypot(u[0] - delta[0], u[1] - delta[1]) < 1e-9,
              `every interior point translates by delta, failed at [${p}]`);
  }
  const outside = elasticField(square, [4000, 0], delta, 3000, DEFAULT_NU);
  assert.ok(Math.hypot(outside[0], outside[1]) < Math.hypot(delta[0], delta[1]),
            "a point outside moves less than the region itself");
}
console.log("rigid core: interior translates uniformly ok");

// ----------------------------------------------------- authoring a lattice
function lattice(n) {
  const src = gridNodes(extent, n, n);
  const R = gridRadius(extent, n, n);
  return { src, R, fac: buildWarp(src, R), dst0: src.map((p) => [p[0], p[1]]) };
}

{
  const n = 7;
  const { src, R, fac, dst0 } = lattice(n);
  const kinds = nodeKinds(n, n);
  const dst = elasticPushGrid(src, dst0, square, [0, 0], [600, 0],
                              defaultEps(extent), DEFAULT_NU, extent, n, n,
                              MAX_DISP_FRAC_CEILING, R, fac);

  const v = validateGrid(src, dst, R, extent, n, n, MAX_DISP_FRAC_CEILING);
  assert.ok(v.ok, `authored grid must validate at its own budget: ${v.reason}`);
  assert.ok(v.nMoved > 0, "the push must actually move nodes");

  for (let i = 0; i < src.length; i++) {
    if (kinds[i] !== NODE_CORNER) continue;
    assert.ok(Math.hypot(dst[i][0] - src[i][0], dst[i][1] - src[i][1]) < 1e-9,
              "corners stay pinned, so the sampling box keeps its shape");
  }

  // the same push is illegal on the default budget -- it is drastic by design
  const strict = validateGrid(src, dst, R, extent, n, n);
  assert.ok(!strict.ok && strict.reason.includes("displacement exceeds limit"),
            `expected the default cap to refuse this push, got "${strict.reason}"`);
}
console.log("authoring: 7x7 grid validates at the raised budget, corners pinned ok");

// ------------------------------------------------- anchor tracks the cursor
{
  // A region far SMALLER than one lattice cell: no node sits inside it, so
  // without the anchor-matching rescale the grab would visibly lag the cursor.
  const n = 5;
  const { src, R, fac, dst0 } = lattice(n);
  const tiny = [[-150, -120], [150, -120], [150, 120], [-150, 120], [-150, -120]];
  const anchor = [0, 0];
  const delta = [500, 0];

  const dst = elasticPushGrid(src, dst0, tiny, anchor, delta, defaultEps(extent),
                              DEFAULT_NU, extent, n, n, MAX_DISP_FRAC_CEILING,
                              R, fac);
  assert.ok(validateGrid(src, dst, R, extent, n, n, MAX_DISP_FRAC_CEILING).ok,
            "the rescaled grid is still legal");

  const got = forwardWarp(src, dst, R, [anchor])[0];
  const moved = Math.hypot(got[0] - anchor[0], got[1] - anchor[1]);
  const want = Math.hypot(delta[0], delta[1]);
  assert.ok(moved > want * 0.75,
            `grab should track the cursor: wanted ${want}, moved ${moved.toFixed(1)}`);
  assert.ok(moved <= want * MAX_ANCHOR_GAIN + 1e-6, "and must not overshoot wildly");
}
console.log("anchor matching: a sub-cell region still follows the cursor ok");

console.log("elastic push tests passed");
