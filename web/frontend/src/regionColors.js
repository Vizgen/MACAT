// Step 5's region overlay in the RANK palette rather than the Allen one.
//
// The Allen palette is anatomical but low in contrast: neighbouring regions
// often get near-identical muted hues, which is hard to align against on a dark,
// busy background (2026-10-21). The rank palette is the one Results'
// "Explore cells" and the static figure use: high-contrast golden-angle hues,
// handed out by size, so the biggest region gets the first colour.
//
// Kept framework-free so tests/test_region_colors.mjs can pin the palette to
// its Python original.

/** Port of allen_alignment.figures._leaf_result_color, exactly: the same hue,
 *  saturation and value for the same rank, so "rank colours" means the same
 *  colours here as in Results. */
export function rankColor(rank) {
  const h = (0.58 + rank * 0.618033988749895) % 1.0;
  const s = [0.78, 0.62, 0.88][rank % 3];
  const v = [0.82, 0.95][Math.floor(rank / 3) % 2];
  // colorsys.hsv_to_rgb
  const i = Math.floor(h * 6.0);
  const f = h * 6.0 - i;
  const p = v * (1.0 - s), q = v * (1.0 - s * f), t = v * (1.0 - s * (1.0 - f));
  const [r, g, b] = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][i % 6];
  const hex = (x) => Math.round(x * 255).toString(16).toUpperCase().padStart(2, "0");
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

function ringArea(points) {
  let a = 0;
  for (let k = 0; k + 1 < points.length; k += 1) {
    a += points[k][0] * points[k + 1][1] - points[k + 1][0] * points[k][1];
  }
  return Math.abs(a) / 2;
}

/** A feature's area: the server's measure when it sent one, else its rings. */
function featureArea(f) {
  if (Number.isFinite(f.area_um2)) return f.area_um2;
  let a = 0;
  for (const r of f.rings || []) {
    a += (r.role === "hole" ? -1 : 1) * ringArea(r.points || []);
  }
  return Math.max(a, 0);
}

/** What one region is called across its components: the guide family in the
 *  Landmarks view, the acronym in Depth 2-4. Null for outlines and midlines,
 *  which keep their own colours. */
function regionKey(f) {
  if (f.kind === "alignment_region") return f.guide_family || f.acronym;
  if (f.kind === "region") return f.acronym;
  return null;
}

/** Recolour one view's features, and its labels, in the rank palette: regions
 *  ranked by total area, largest first, every component of a region in its
 *  colour. The Allen colour is kept as `allen_color`. Returns new arrays;
 *  inputs are not modified. */
export function rankRecolor(features, labels = []) {
  const area = new Map();
  for (const f of features || []) {
    const k = regionKey(f);
    if (k != null) area.set(k, (area.get(k) || 0) + featureArea(f));
  }
  const order = [...area.entries()]
    .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
  const color = new Map(order.map(([k], i) => [k, rankColor(i)]));
  const feats = (features || []).map((f) => {
    const k = regionKey(f);
    return k != null && color.has(k) ? { ...f, allen_color: f.color, color: color.get(k) } : f;
  });
  const labs = (labels || []).map((L) => (color.has(L.acronym)
    ? { ...L, allen_color: L.color, color: color.get(L.acronym) } : L));
  return { features: feats, labels: labs, colors: color };
}
