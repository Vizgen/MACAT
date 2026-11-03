// Global in-plane affine used by Manual Curation. This is the SINGLE source of
// truth shared with the backend: `pipeline_api.affine_from_params` builds the
// identical 2x3 matrix with the identical composition order, so the browser
// preview and the finalize raster agree to numerical noise.
//
// All coordinates are plane microns [lr, si]. The transform is:
//     A = R(theta) @ diag(sx, sy)              (rotation then anisotropic scale)
//     b = center + t - A @ center              (scale/rotate ABOUT a fixed center)
//     forward(p) = A @ p + b
// There is no shear, no perspective and no reflection: det = sx*sy > 0 always
// because both scales are clamped strictly positive.

export const SCALE_MIN = 0.2;
export const SCALE_MAX = 5.0;

export function clampScale(s) {
  if (!Number.isFinite(s)) return 1;
  return Math.min(SCALE_MAX, Math.max(SCALE_MIN, s));
}

// Build the forward 2x3 affine [[a, b, e], [c, d, f]].
export function affineMatrix(cx, cy, tx, ty, rotDeg, sx, sy) {
  sx = clampScale(sx);
  sy = clampScale(sy);
  const th = (rotDeg * Math.PI) / 180;
  const cos = Math.cos(th), sin = Math.sin(th);
  // A = R(theta) @ diag(sx, sy)
  const a = cos * sx, b = -sin * sy;
  const c = sin * sx, d = cos * sy;
  // b_vec = center + t - A @ center
  const e = cx + tx - (a * cx + b * cy);
  const f = cy + ty - (c * cx + d * cy);
  return [[a, b, e], [c, d, f]];
}

export function applyAffine(M, p) {
  return [
    M[0][0] * p[0] + M[0][1] * p[1] + M[0][2],
    M[1][0] * p[0] + M[1][1] * p[1] + M[1][2],
  ];
}

export function invertAffine(M) {
  const a = M[0][0], b = M[0][1], e = M[0][2];
  const c = M[1][0], d = M[1][1], f = M[1][2];
  const det = a * d - b * c;
  const ia = d / det, ib = -b / det, ic = -c / det, id = a / det;
  return [
    [ia, ib, -(ia * e + ib * f)],
    [ic, id, -(ic * e + id * f)],
  ];
}

export const IDENTITY_PARAMS = { tx: 0, ty: 0, rotDeg: 0, sx: 1, sy: 1 };

export function isIdentity(p, eps = 1e-9) {
  return (
    Math.abs(p.tx) < eps && Math.abs(p.ty) < eps &&
    Math.abs(p.rotDeg) < eps &&
    Math.abs(p.sx - 1) < eps && Math.abs(p.sy - 1) < eps
  );
}

// ---- pure handle helpers (UX only; they only ever emit new {sx,sy,rotDeg}) ---
// The unrotated box has half-extents (halfW, halfH) about the fixed center. The
// translated center in world microns is ct = [cx + tx, cy + ty]. Local axes come
// from the current rotation; a handle drag reprojects the pointer onto them and
// solves for the scale/rotation that puts the handle under the cursor. tx/ty are
// never changed by a scale/rotate handle, so scaling stays symmetric about center.
export function localAxes(rotDeg) {
  const th = (rotDeg * Math.PI) / 180;
  const cos = Math.cos(th), sin = Math.sin(th);
  return { x: [cos, sin], y: [-sin, cos] };
}

const dot = (a, b) => a[0] * b[0] + a[1] * b[1];

// scaleFromHandle: which ∈ {'x','y','both'}. Returns clamped {sx?, sy?}.
export function scaleFromHandle(which, pointer, ct, rotDeg, halfW, halfH) {
  const ax = localAxes(rotDeg);
  const off = [pointer[0] - ct[0], pointer[1] - ct[1]];
  const out = {};
  if (which === "x" || which === "both")
    out.sx = clampScale(Math.abs(dot(off, ax.x)) / halfW);
  if (which === "y" || which === "both")
    out.sy = clampScale(Math.abs(dot(off, ax.y)) / halfH);
  return out;
}

// Resize with the OPPOSITE edge/corner fixed. This is the familiar behaviour of
// image editors: pulling the bottom handle changes only the bottom, rather than
// expanding symmetrically around the centre. signX/signY identify the dragged
// side (-1 left/top, +1 right/bottom, 0 for an unaffected axis).
//
// Returns scale plus the translation delta needed to keep the opposite anchor at
// exactly the same world coordinate under the existing rotation.
export function anchoredScaleFromHandle(
  which, pointer, center, params, halfW, halfH, signX = 0, signY = 0,
) {
  const ax = localAxes(params.rotDeg);
  const anchorLocal = [
    (which === "x" || which === "both") ? -signX * halfW : 0,
    (which === "y" || which === "both") ? -signY * halfH : 0,
  ];
  const anchorWorld = [
    center[0] + params.tx
      + ax.x[0] * params.sx * anchorLocal[0]
      + ax.y[0] * params.sy * anchorLocal[1],
    center[1] + params.ty
      + ax.x[1] * params.sx * anchorLocal[0]
      + ax.y[1] * params.sy * anchorLocal[1],
  ];
  const off = [pointer[0] - anchorWorld[0], pointer[1] - anchorWorld[1]];
  let sx = params.sx, sy = params.sy;
  if (which === "x" || which === "both")
    sx = clampScale((signX * dot(off, ax.x)) / (2 * halfW));
  if (which === "y" || which === "both")
    sy = clampScale((signY * dot(off, ax.y)) / (2 * halfH));

  const localDx = (params.sx - sx) * anchorLocal[0];
  const localDy = (params.sy - sy) * anchorLocal[1];
  return {
    sx, sy, rotDeg: params.rotDeg,
    tx: params.tx + ax.x[0] * localDx + ax.y[0] * localDy,
    ty: params.ty + ax.x[1] * localDx + ax.y[1] * localDy,
  };
}

// rotationFromHandle: the handle sits above the top edge (local -y). At rotDeg=0
// the pointer offset points straight up (si negative), which we map to 0 degrees.
export function rotationFromHandle(pointer, ct) {
  const off = [pointer[0] - ct[0], pointer[1] - ct[1]];
  return (Math.atan2(off[1], off[0]) * 180) / Math.PI + 90;
}
