// Spatial index over the per-cell points, so hovering can name a cell without
// scanning all 85,000 of them on every mouse move.
//
// A uniform grid rather than a k-d tree: the points are cells in a tissue
// section, so they are near-uniformly dense over the region that matters, which
// is exactly the case a grid handles best and a tree's extra structure buys
// nothing for. Framework-free so tests/test_cellindex.mjs can drive it.

/** Build the index. `x`/`y` are quantised uint16 arrays, `cells` the target
 *  number of points per bucket -- 8 keeps a bucket scan short while keeping the
 *  bucket count near the point count rather than far above it. */
export function buildIndex(x, y, cellsPerBucket = 8) {
  const n = x.length;
  // 65536 is the quantisation range the server used; deriving the grid from it
  // rather than from the data means the index does not depend on an outlier.
  const cols = Math.max(1, Math.round(Math.sqrt(n / cellsPerBucket)));
  const step = 65536 / cols;
  const counts = new Int32Array(cols * cols + 1);
  const bucketOf = (i) => {
    const cx = Math.min(cols - 1, (x[i] / step) | 0);
    const cy = Math.min(cols - 1, (y[i] / step) | 0);
    return cy * cols + cx;
  };
  for (let i = 0; i < n; i++) counts[bucketOf(i) + 1]++;
  for (let b = 0; b < cols * cols; b++) counts[b + 1] += counts[b];
  const order = new Int32Array(n);
  const fill = counts.slice(0, cols * cols);
  for (let i = 0; i < n; i++) order[fill[bucketOf(i)]++] = i;
  return { x, y, cols, step, starts: counts, order, n };
}

/** Index of the point nearest (qx, qy), or -1 if none within `maxDist`.
 *
 *  Searches outward in rings of buckets and stops as soon as the next ring
 *  cannot beat the best hit so far. Without that check a sparse area near the
 *  section edge would scan the whole grid on every mouse move.
 */
export function nearest(index, qx, qy, maxDist = Infinity, visible = null) {
  const { x, y, cols, step, starts, order } = index;
  const cx = Math.min(cols - 1, Math.max(0, (qx / step) | 0));
  const cy = Math.min(cols - 1, Math.max(0, (qy / step) | 0));
  let best = -1;
  let bestD2 = maxDist === Infinity ? Infinity : maxDist * maxDist;
  for (let ring = 0; ring < cols; ring++) {
    // a whole ring further out than the current best cannot contain a better
    // point, because every bucket in it is at least (ring-1)*step away
    if (best >= 0 && ((ring - 1) * step) ** 2 > bestD2) break;
    let touched = false;
    for (let gy = cy - ring; gy <= cy + ring; gy++) {
      if (gy < 0 || gy >= cols) continue;
      for (let gx = cx - ring; gx <= cx + ring; gx++) {
        if (gx < 0 || gx >= cols) continue;
        // only the ring's perimeter; the interior was covered by earlier rings
        if (ring > 0 && Math.abs(gy - cy) !== ring && Math.abs(gx - cx) !== ring) {
          continue;
        }
        touched = true;
        const b = gy * cols + gx;
        for (let k = starts[b]; k < starts[b + 1]; k++) {
          const i = order[k];
          if (visible && !visible(i)) continue;
          const dx = x[i] - qx, dy = y[i] - qy;
          const d2 = dx * dx + dy * dy;
          if (d2 < bestD2) { bestD2 = d2; best = i; }
        }
      }
    }
    if (!touched && ring > cols) break;
  }
  return best;
}
