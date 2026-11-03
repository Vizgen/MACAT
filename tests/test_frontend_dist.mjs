// The committed web/frontend/dist/ is exactly what the current source builds to.
//
// Run: node tests/test_frontend_dist.mjs   (Node 20, after `npm ci` in web/frontend)
//
// dist/ is committed so users never need Node: the server hands the browser
// these prebuilt files. That only works if dist/ is rebuilt with every change to
// web/frontend/src. This builds the source into a temporary folder and fails if
// any file differs from, is missing from, or is extra to the committed dist/.
// Vite's output is deterministic (two builds are byte-identical), so the
// comparison is exact. Fix a failure with: cd web/frontend && npm run build.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = fileURLToPath(new URL("../web/frontend/", import.meta.url));
const COMMITTED = join(FRONTEND, "dist");

let passed = 0;
const test = (n, f) => { f(); passed += 1; console.log("  ok  " + n); };

function files(root) {
  const out = new Map();
  const walk = (d) => {
    for (const name of readdirSync(d)) {
      const p = join(d, name);
      if (statSync(p).isDirectory()) walk(p);
      else out.set(relative(root, p), readFileSync(p));
    }
  };
  walk(root);
  return out;
}

const fresh = mkdtempSync(join(tmpdir(), "macat_dist_"));
try {
  execFileSync("npx", ["vite", "build", "--outDir", fresh, "--emptyOutDir", "--logLevel", "error"],
               { cwd: FRONTEND, stdio: "inherit" });
  const want = files(fresh);
  const have = files(COMMITTED);

  test("the committed dist/ has exactly the files the source builds", () => {
    assert.deepEqual([...have.keys()].sort(), [...want.keys()].sort(),
      "dist/ is stale: run `npm run build` in web/frontend and commit dist/");
  });

  test("and every file is byte-identical", () => {
    const stale = [...want.keys()].filter((k) => !want.get(k).equals(have.get(k)));
    assert.deepEqual(stale, [],
      "dist/ is stale: run `npm run build` in web/frontend and commit dist/");
  });
} finally {
  rmSync(fresh, { recursive: true, force: true });
}

console.log(`${passed} passed`);
