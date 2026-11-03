"""Isolated finalize worker.

Runs the heavy Wendland-CSRBF finalize in a short-lived subprocess so a crash or
OOM cannot take down the API server, and so its peak memory is fully reclaimed on
exit. Reads a JSON args file, writes a JSON result file.

Usage: python -m web._finalize_worker <args.json> <result.json>
  args: {path, exp, src, dst, R, out_dir, tag}
"""
import os
import sys
import json

# Keep BLAS single-threaded: avoids thread oversubscription on the shared host.
# Assign rather than setdefault because the service environment may already set
# OPENBLAS_NUM_THREADS to the host CPU count; the isolated worker inherits it.
for _thread_env in (
    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
):
    os.environ[_thread_env] = "1"

import pipeline_api as api  # noqa: E402


def main():
    args_path, result_path = sys.argv[1], sys.argv[2]
    with open(args_path) as f:
        a = json.load(f)

    b = api.load_bundle(a["path"])
    if a.get("affine") is not None:
        # global affine + OPTIONAL sparse CSRBF warp grid (Phase 2). warp=None
        # reduces this to the pure global-affine finalize.
        man = api.finalize_composite(
            b, a["exp"], a["affine"], a.get("warp"), a["out_dir"], tag=a["tag"],
            atlas=api.get_atlas(),
        )
    else:
        man = api.finalize_csrbf(
            b, a["exp"], a["src"], a["dst"], float(a["R"]),
            a["out_dir"], tag=a["tag"], atlas=api.get_atlas(),
        )
    out = {
        "coverage": man["coverage"],
        "n_mapped": man["n_mapped"],
        "n_total": man.get("n_total"),
        "fig_path": man.get("fig_path"),
        # the per-panel archive; a whitelist means a new field is dropped
        # silently unless it is named here, which is how it went missing once
        "figure_zip_path": man.get("figure_zip_path"),
        "top_regions": man["top_regions"],
        "atlas_overhang": man.get("atlas_overhang"),
        "csv_path": man["csv_path"],
        "geojson_path": man["geojson_path"],
        "warp": man.get("warp"),
    }
    with open(result_path, "w") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main()
