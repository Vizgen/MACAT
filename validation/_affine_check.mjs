// Cross-language affine parity harness. Reads a JSON payload
//   {cases:[{cx,cy,tx,ty,rot_deg,sx,sy,pts:[[lr,si],...]}, ...]}
// from the file given as argv[2], applies the frontend affine (the exact math the
// Manual Curation canvas uses), and prints {results:[{M, out:[[lr,si],...]}]} so
// validate can compare it to pipeline_api.affine_from_params / affine_apply.
import { readFileSync } from "node:fs";
import { affineMatrix, applyAffine } from "../web/frontend/src/affine.js";

const payload = JSON.parse(readFileSync(process.argv[2], "utf8"));
const results = payload.cases.map((c) => {
  const M = affineMatrix(c.cx, c.cy, c.tx, c.ty, c.rot_deg, c.sx, c.sy);
  const out = (c.pts || []).map((p) => applyAffine(M, p));
  return { M, out };
});
process.stdout.write(JSON.stringify({ results }));
