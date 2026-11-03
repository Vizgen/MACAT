"""Detached registration worker.

Runs the automatic 3D-to-slice registration for one job in its own session so
it survives request teardown and cannot OOM-kill the API server. Streams progress
to <job>/register.log, then writes the result back into job.json and flips the
job state to AUTO_REGISTRATION_DONE (or FAILED).

Usage: python -m web._register_worker <job_id>
"""
import os
import sys
import time
import traceback

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import pipeline_api as api        # noqa: E402
import web.jobs as jobs           # noqa: E402


def main():
    jid = sys.argv[1]
    jd = jobs.job_dir(jid)
    log_path = os.path.join(jd, "register.log")
    status_path = os.path.join(jd, "register.status")

    def say(msg):
        with open(log_path, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def set_status(s):
        with open(status_path, "w") as f:
            f.write(s)

    job = jobs.load(jid)
    if job is None:
        return
    try:
        o = job["orientation"]
        flip = None if o["flip"] in (None, "none") else o["flip"]
        say(f"starting registration: AP={job['ap_idx']}, "
            f"angle={o['angle']}, flip={o['flip']}")
        # The plane lives on the orientation; a job written before the field
        # existed has none, and register_region defaults it to coronal.
        plane = o.get("plane")
        say(f"plane={plane or 'coronal'}")
        # Also on the orientation, and absent on every job before 2026-10-20,
        # which were all registered as whole sections.
        hemisection = o.get("hemisection")
        if hemisection:
            say(f"hemisection: {hemisection} half")
        res = api.register_region(
            job["exp_path"], float(o["angle"]), flip, int(job["ap_idx"]),
            plane=plane, tag=job["tag"], out_dir=job["out_dir"],
            atlas=api.get_atlas(), progress=say, hemisection=hemisection,
        )
        # keep only JSON-serializable, path/metric fields in job.json
        job = jobs.load(jid)
        job["register"]["status"] = "done"
        job["register"]["result"] = {
            k: res[k] for k in (
                "csv_path", "fig_path", "figure_zip_path", "fit_path", "bundle_path",
                "n_cells", "n_mapped", "coverage", "tilt_deg",
                "scale_ap", "scale_si", "scale_lr", "top_regions",
                "selected_ap_idx", "selected_ap_um",
                "sampled_ap_idx", "sampled_ap_um",
                # a whitelist: a field not named here never reaches the UI
                "atlas_overhang", "hemisection",
                # which atlas the fit used; report.zip's manifest records it
                "atlas_provenance",
            ) if k in res
        }
        job["state"] = "AUTO_REGISTRATION_DONE"
        job["error"] = None
        jobs.save(job)
        set_status("done")
        say(f"done: {res['coverage']*100:.1f}% labeled, {res['n_mapped']:,} cells")
    except Exception as e:
        say("FAILED: " + str(e))
        say(traceback.format_exc())
        job = jobs.load(jid)
        if job is not None:
            job["register"]["status"] = "failed"
            job["state"] = "FAILED"
            job["error"] = str(e)
            jobs.save(job)
        set_status("failed")


if __name__ == "__main__":
    main()
